"""Durable bounded Kubernetes list/watch projection; collection grants no authority.

A transport session owns ordering. Resource versions are opaque. After process
restart or any stream error a full list is mandatory; persisted rows are evidence,
not a claim that a disconnected watcher is current. SQLite serializes writers.
"""
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import closing
import json
import sqlite3
import re
import sys
import time
import uuid
import hmac
import secrets
from pathlib import Path
from urllib.parse import quote
from .topology import need, bounded, MAX_RECORDS, capped_expiry, current
from .observations import _identifier, _utc, _digest
from .expiry_ledger import check_generation, initialize_expiry_ledger

# Forty-five seconds permits the bounded 31-second TLS call plus scheduling slack.
LEASE_SECONDS = 45

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
        if sys.platform == 'darwin':
            self.db.execute('PRAGMA fullfsync=ON')
            self.db.execute('PRAGMA checkpoint_fullfsync=ON')
        self.db.execute('CREATE TABLE IF NOT EXISTS projection (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        if 'digest' not in {row[1] for row in self.db.execute('PRAGMA table_info(projection)')}:
            self.db.execute('ALTER TABLE projection ADD COLUMN digest TEXT')
        self.db.execute('CREATE TABLE IF NOT EXISTS store_identity (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO store_identity VALUES(1,?)', (str(uuid.uuid4()),))
        self.owner_id = str(uuid.uuid4())
        self.closed = False
        self.db.execute('CREATE TABLE IF NOT EXISTS lease (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, heartbeat REAL NOT NULL, live INTEGER NOT NULL)')
        if 'generation' not in {row[1] for row in self.db.execute('PRAGMA table_info(lease)')}:
            self.db.execute('ALTER TABLE lease ADD COLUMN generation TEXT')
        self.generation = str(uuid.uuid4())
        self.last_tick = time.monotonic()
        self.session = None
        self.transport_digest = None
        try:
            self._transaction(self._acquire)
        except BaseException:
            self.db.close()
            self.closed = True
            raise

    def _acquire(self, prior):
        prior = self._disconnect(prior)
        self.db.execute('INSERT OR REPLACE INTO lease VALUES(1,?,?,1,?)',
                        (self.owner_id, time.time(), self.generation))
        return prior

    def _check_owner(self):
        row = self.db.execute('SELECT owner,generation,live,heartbeat FROM lease WHERE id=1').fetchone()
        need(row is not None and row[:2] == (self.owner_id, self.generation), 'collector lease lost')
        valid = row[2] == 1 and 0 <= time.time() - row[3] < LEASE_SECONDS
        valid = valid and 0 <= time.monotonic() - self.last_tick < LEASE_SECONDS
        if not valid:
            self.db.execute('UPDATE lease SET live=0 WHERE id=1')
            self.db.execute('COMMIT')
            need(False, 'collector lease closed or expired')

    def _disconnect(self, prior):
        if prior:
            need(prior['scope'] == self.scope, 'watch store scope changed')
            prior['resync_required'] = True
        return prior

    def heartbeat(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._check_owner()
            self.db.execute('UPDATE lease SET heartbeat=? WHERE id=1', (time.time(),))
            self.db.execute('COMMIT')
            self.last_tick = time.monotonic()
        except BaseException:
            if self.db.in_transaction: self.db.execute('ROLLBACK')
            raise

    def close(self):
        if not self.closed:
            try:
                self.db.execute('UPDATE lease SET live=0 WHERE id=1 AND owner=?', (self.owner_id,))
            finally:
                self.closed = True
                self.db.close()

    def _transaction(self, update):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if update != self._acquire:
                self._check_owner()
            row = self.db.execute('SELECT body,digest FROM projection WHERE id=1').fetchone()
            prior = json.loads(row[0]) if row else None
            if row:
                need(row[1] is None or row[1] == _digest(prior), 'watch integrity mismatch')
                if row[1] is None: prior['resync_required'] = True
            value = update(prior)
            if value is not None:
                bounded(value)
                self.db.execute('INSERT OR REPLACE INTO projection VALUES(1,?,?)', (json.dumps(value, sort_keys=True), _digest(value)))
            self.db.execute('COMMIT')
            return value
        except BaseException:
            if self.db.in_transaction: self.db.execute('ROLLBACK')
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
        try:
            return self._relist(payload, observed_at, expires_at)
        except BaseException:
            self.fail()
            raise

    def _relist(self, payload, observed_at, expires_at):
        bounded(payload)
        expires_at = capped_expiry(observed_at, expires_at)
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
            return dict(scope=self.scope, generation=self.generation, session=session, resource_version=rv, records=records,
                        observed_at=observed_at, expires_at=expires_at, resync_required=False,
                        events=0, last_event=None, transport_digest=self.transport_digest)
        self._transaction(update)
        self.session = session
        self.heartbeat()

    def apply(self, events, observed_at, expires_at):
        """Atomically accept an ordered bounded batch from the current TLS stream.

        Overflow, HTTP 410/ERROR, UID reuse, duplicate RV with changed contents or
        stream loss taints persisted state. Recovery requires relist. A complete
        watch frame is required; transport must never silently truncate frames.
        """
        try:
            bounded(events)
            need(type(events) is list and len(events) <= 1024, 'watch queue overflow')
            expires_at = capped_expiry(observed_at, expires_at)
            def update(prior):
                need(prior is not None and self.session == prior['session'] and not prior['resync_required'], 'relist required')
                need(_utc(observed_at) >= _utc(prior['observed_at']), 'watch clock regressed')
                for event in events:
                    need(type(event) is dict and set(event) == {'type','object'}, 'invalid watch frame')
                    typ = event['type']; need(typ in ('ADDED','MODIFIED','DELETED','BOOKMARK'), 'watch lost: relist required')
                    if typ == 'BOOKMARK':
                        version, kind = KINDS[self.scope[2]]
                        need(type(event['object']) is dict and event['object'].get('apiVersion') == version
                             and event['object'].get('kind') == kind, 'bookmark type mismatch')
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
                if events:
                    prior['observed_at'], prior['expires_at'] = observed_at, expires_at
                return prior
            result = self._transaction(update)
            self.heartbeat()
            return result
        except BaseException:
            self.fail()
            raise

    def snapshot(self, now):
        row = self.db.execute('SELECT body,digest FROM projection WHERE id=1').fetchone()
        need(row is not None, 'no topology collection')
        value = json.loads(row[0])
        need(row[1] == _digest(value), 'watch integrity mismatch')
        return _snapshot(value, now)


def _snapshot(value, now):
    issues = []
    if value['resync_required']: issues.append('resync_required')
    if not current(value['observed_at'], value['expires_at'], now): issues.append('stale_or_future')
    value.update(schema='dimaggi-kubernetes-watch/v1', issues=issues, execution_authorized=False)
    value['snapshot_id'] = _digest(value)
    return value

_READ_KEY = secrets.token_bytes(32)


class CurrentSnapshot(dict):
    """Process-local reader receipt; serialization does not preserve verification."""


def _seal_snapshot(value, path, expiry_ledger):
    result = CurrentSnapshot(value)
    result._path = str(Path(path).resolve())
    result._expiry_ledger = str(Path(expiry_ledger).absolute())
    result._seal = hmac.digest(_READ_KEY, (result._path + result._expiry_ledger + _digest(value)).encode(), 'sha256')
    return result


def verified_snapshot(value, *, tenant, cluster, now):
    need(type(value) is CurrentSnapshot, 'verified WatchStore reader receipt required')
    expected = hmac.digest(_READ_KEY, (value._path + value._expiry_ledger + _digest(dict(value))).encode(), 'sha256')
    need(hmac.compare_digest(value._seal, expected), 'modified WatchStore reader receipt')
    fresh = read_current(value._path, expiry_ledger=value._expiry_ledger, tenant=tenant, cluster=cluster, collection='nodes', now=now)
    need(fresh == value, 'WatchStore changed since read')
    return fresh


def read_current(path, *, expiry_ledger, tenant, cluster, collection, namespace='', now):
    """Read the collector database read-only; retain expiry in the reader ledger."""
    try:
        uri = 'file:' + quote(str(Path(path).resolve()), safe='/') + '?mode=ro'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            db.execute('BEGIN')
            need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'watch integrity check failed')
            row = db.execute('SELECT body,digest FROM projection WHERE id=1').fetchone()
            need(row is not None, 'no topology collection')
            value = json.loads(row[0])
            need(row[1] == _digest(value), 'watch integrity mismatch')
            scope = [tenant, cluster, collection, namespace]
            need(value['scope'] == scope, 'watch scope mismatch')
            lease = db.execute('SELECT owner,heartbeat,live,generation FROM lease WHERE id=1').fetchone()
            need(lease is not None and lease[0] and lease[3], 'collector lease missing')
            need(value.get('generation') == lease[3], 'projection generation mismatch')
            identity = db.execute('SELECT identity FROM store_identity WHERE id=1').fetchone()
            need(identity is not None and type(identity[0]) is str and identity[0], 'store identity missing')
            wall = time.time()
            live = lease[2] == 1 and 0 <= wall - lease[1] < LEASE_SECONDS
            check_generation(expiry_ledger, identity[0], scope, lease[3], live)
            need(abs(_utc(now).timestamp() - wall) <= 2, 'reader clock differs from wall clock')
            result = _snapshot(value, datetime.fromtimestamp(wall, timezone.utc).isoformat().replace('+00:00', 'Z'))
            need(not result['issues'], 'current topology required: ' + ','.join(result['issues']))
            need(re.fullmatch(r'[0-9]{1,32}', result['resource_version']) is not None, 'numeric resource version required')
            return _seal_snapshot(result, path, expiry_ledger)
    except (sqlite3.Error, KeyError, TypeError, OSError, UnicodeError) as exc:
        raise ValueError('current topology unavailable') from exc


def validate_inventory_agreement(inventories):
    """Refuse contradictory complete inventories covering the same collection scope."""
    need(type(inventories) is list and 1 <= len(inventories) <= 32, 'bounded inventories required')
    seen = {}
    for inventory in inventories:
        scope = tuple(inventory['scope'])
        records = inventory['records']
        need(scope not in seen or seen[scope] == records, 'source_conflict')
        seen[scope] = records
