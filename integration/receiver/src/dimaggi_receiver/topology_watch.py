"""Durable bounded Kubernetes list/watch projection; collection grants no authority.

A transport session owns ordering. Resource versions are opaque. After process
restart or any stream error a full list is mandatory; persisted rows are evidence,
not a claim that a disconnected watcher is current. SQLite serializes writers.
"""
from copy import deepcopy
import json
import sqlite3
from .topology import need, bounded, MAX_RECORDS
from .observations import _identifier, _utc, _digest

KINDS = {'nodes': ('v1', 'Node'), 'resourceslices': ('resource.k8s.io/v1', 'ResourceSlice'),
         'resourceclaims': ('resource.k8s.io/v1', 'ResourceClaim')}


class WatchStore:
    def __init__(self, path, tenant, cluster, collection, namespace=''):
        need(collection in KINDS, 'unsupported collection')
        self.scope = [ _identifier(tenant, 'tenant'), _identifier(cluster, 'cluster'), collection, namespace ]
        need((collection == 'resourceclaims') == bool(namespace), 'claims require explicit namespace')
        if namespace: _identifier(namespace, 'namespace')
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS projection (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        self.session = None
        self.transport_digest = None
        self._transaction(lambda prior: self._disconnect(prior))

    def _disconnect(self, prior):
        if prior:
            need(prior['scope'] == self.scope, 'watch store scope changed')
            prior['resync_required'] = True
        return prior

    def close(self): self.db.close()

    def _transaction(self, update):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT body FROM projection WHERE id=1').fetchone()
            value = update(json.loads(row[0]) if row else None)
            if value is not None:
                bounded(value)
                self.db.execute('INSERT OR REPLACE INTO projection VALUES(1,?)', (json.dumps(value, sort_keys=True),))
            self.db.execute('COMMIT')
            return value
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def fail(self):
        self.session = None
        self._transaction(self._disconnect)

    def _object(self, obj):
        need(type(obj) is dict, 'object required')
        obj = deepcopy(obj)
        version, kind = KINDS[self.scope[2]]
        for key, value in [('apiVersion', version), ('kind', kind)]:
            need(obj.get(key, value) == value, 'mixed resource type')
            obj[key] = value
        m = obj.get('metadata', {})
        for key in ('name', 'uid', 'resourceVersion'): _identifier(m.get(key), key)
        need(m.get('namespace', '') == self.scope[3], 'resource namespace mismatch')
        return obj

    def relist(self, payload, observed_at, expires_at):
        bounded(payload)
        need(_utc(observed_at) < _utc(expires_at), 'invalid validity')
        version, kind = KINDS[self.scope[2]]
        need(payload.get('apiVersion') == version and payload.get('kind') == kind+'List', 'list type mismatch')
        meta = payload.get('metadata', {})
        rv = _identifier(meta.get('resourceVersion'), 'list resourceVersion')
        need(not meta.get('continue') and meta.get('remainingItemCount', 0) == 0, 'incomplete list')
        rows = payload.get('items')
        need(type(rows) is list and len(rows) <= MAX_RECORDS, 'list bound exceeded')
        records = {}
        for raw in rows:
            obj = self._object(raw); name = obj['metadata']['name']
            need(name not in records, 'duplicate object name')
            records[name] = obj
        import uuid
        session = str(uuid.uuid4())
        def update(prior):
            if prior: need(_utc(observed_at) >= _utc(prior['observed_at']), 'collection clock regressed')
            return dict(scope=self.scope, session=session, resource_version=rv, records=records,
                        observed_at=observed_at, expires_at=expires_at, resync_required=False,
                        events=0, last_event=None, transport_digest=self.transport_digest)
        self._transaction(update)
        self.session = session

    def apply(self, events, observed_at, expires_at):
        """Atomically accept an ordered bounded batch from the current TLS stream.

        Overflow, HTTP 410/ERROR, UID reuse, duplicate RV with changed contents or
        stream loss taints persisted state. Recovery requires relist. A complete
        watch frame is required; transport must never silently truncate frames.
        """
        try:
            bounded(events)
            need(type(events) is list and len(events) <= 1024, 'watch queue overflow')
            need(_utc(observed_at) < _utc(expires_at), 'invalid validity')
            def update(prior):
                need(prior is not None and self.session == prior['session'] and not prior['resync_required'], 'relist required')
                need(_utc(observed_at) >= _utc(prior['observed_at']), 'watch clock regressed')
                for event in events:
                    need(type(event) is dict and set(event) == {'type','object'}, 'invalid watch frame')
                    typ = event['type']; need(typ in ('ADDED','MODIFIED','DELETED','BOOKMARK'), 'watch lost: relist required')
                    if typ == 'BOOKMARK':
                        rv = _identifier(event['object'].get('metadata', {}).get('resourceVersion'), 'bookmark version')
                    else:
                        obj = self._object(event['object']); m = obj['metadata']; rv = m['resourceVersion']; name = m['name']
                        old = prior['records'].get(name)
                        if rv == prior['resource_version'] and prior['last_event'] == _digest(event): continue
                        need(rv != prior['resource_version'], 'conflicting resource version')
                        if typ == 'ADDED': need(old is None, 'added identity already exists')
                        else: need(old is not None and old['metadata']['uid'] == m['uid'], 'object UID mismatch')
                        if old: need(rv != old['metadata']['resourceVersion'], 'object version conflict')
                        if typ == 'DELETED': del prior['records'][name]
                        else: prior['records'][name] = obj
                    prior['resource_version'] = rv
                    prior['last_event'] = _digest(event)
                    prior['events'] += 1
                    need(len(prior['records']) <= MAX_RECORDS, 'object bound exceeded')
                prior['observed_at'], prior['expires_at'] = observed_at, expires_at
                return prior
            return self._transaction(update)
        except BaseException:
            self.fail()
            raise

    def snapshot(self, now):
        row = self.db.execute('SELECT body FROM projection WHERE id=1').fetchone()
        need(row is not None, 'no topology collection')
        value = json.loads(row[0]); issues = []
        if value['resync_required']: issues.append('resync_required')
        if not _utc(value['observed_at']) <= _utc(now) < _utc(value['expires_at']): issues.append('stale_or_future')
        value.update(schema='dimaggi-kubernetes-watch/v1', issues=issues, execution_authorized=False)
        value['snapshot_id'] = _digest(value)
        return value
