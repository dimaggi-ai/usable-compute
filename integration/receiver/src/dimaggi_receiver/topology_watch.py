"""Durable bounded Kubernetes list/watch projection; collection grants no authority.

A transport session owns ordering. Resource versions are opaque. After process
restart or any stream error a full list is mandatory; persisted rows are evidence,
not a claim that a disconnected watcher is current. SQLite serializes writers.
"""
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import closing, contextmanager
import json
import fcntl
import os
import stat
import sqlite3
import re
import sys
import time
import uuid
import hmac
import secrets
import tempfile
from pathlib import Path
from urllib.parse import quote
from .topology import need, bounded, MAX_RECORDS, capped_expiry, current
from .observations import _identifier, _utc, _digest
from .expiry_ledger import check_generation, initialize_expiry_ledger

# Forty-five seconds permits the bounded 31-second TLS call plus scheduling slack.
LEASE_SECONDS = 45
CLOCK_TOLERANCE_SECONDS = 2
COMMIT_BOUND_SECONDS = 10

KINDS = {'nodes': ('v1', 'Node'), 'resourceslices': ('resource.k8s.io/v1', 'ResourceSlice'),
         'resourceclaims': ('resource.k8s.io/v1', 'ResourceClaim')}


@contextmanager
def _lease_lock(path, *, writer=False):
    # Only collectors can traverse this private directory. Readers never open
    # this inode or any of the writer database's SQLite lock-bearing files.
    if path is None:  # Private :memory: stores cannot be read by read_current.
        yield
        return
    fd = os.open(str(path) + '.lease-lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(stat.S_ISREG(os.fstat(fd).st_mode), 'regular collector lease lock required')
        deadline = time.perf_counter() + 5
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if writer else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                need(time.perf_counter() < deadline, 'collector lease sampling busy')
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


class WatchStore:
    def __init__(self, path, tenant, cluster, collection, namespace=''):
        need(collection in KINDS, 'unsupported collection')
        self.scope = [ _identifier(tenant, 'tenant'), _identifier(cluster, 'cluster'), collection, namespace ]
        need((collection == 'resourceclaims') == bool(namespace), 'claims require explicit namespace')
        if namespace: _identifier(namespace, 'namespace')
        self.path = None if str(path) == ':memory:' else str(Path(path).resolve())
        self.writer_path = None
        if self.path is not None:
            directory = Path(self.path + '.collector')
            try:
                directory.mkdir(mode=0o700)
                directory.chmod(0o700)
            except FileExistsError:
                pass
            info = directory.lstat()
            need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700
                 and info.st_uid == os.geteuid(), 'private collector directory required')
            self.writer_path = str(directory / 'writer.db')
            fd = os.open(self.writer_path + '.lease-lock',
                         os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            os.close(fd)
        with _lease_lock(self.writer_path, writer=True):
            migrate = self.writer_path is not None and not Path(self.writer_path).exists() and Path(self.path).exists()
            self.db = sqlite3.connect(self.writer_path or ':memory:', timeout=5, isolation_level=None)
            if self.writer_path is not None:
                os.chmod(self.writer_path, 0o600)
            if migrate:
                uri = 'file:' + quote(self.path, safe='/') + '?mode=ro'
                with closing(sqlite3.connect(uri, uri=True, timeout=5)) as prior:
                    prior.backup(self.db)
            if self.path is not None:
                Path(self.path + '.lease-lock').unlink(missing_ok=True)
                self._remove_public_journals()
                self._clean_temporary_copies()
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            if sys.platform == 'darwin':
                self.db.execute('PRAGMA fullfsync=ON')
                self.db.execute('PRAGMA checkpoint_fullfsync=ON')
            self.db.execute('CREATE TABLE IF NOT EXISTS projection (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
            if 'digest' not in {row[1] for row in self.db.execute('PRAGMA table_info(projection)')}:
                self.db.execute('ALTER TABLE projection ADD COLUMN digest TEXT')
            self.db.execute('CREATE TABLE IF NOT EXISTS store_identity (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO store_identity VALUES(1,?)', (str(uuid.uuid4()),))
            self.db.execute('CREATE TABLE IF NOT EXISTS publication_protocol (version INTEGER NOT NULL)')
            self.db.execute('DELETE FROM publication_protocol')
            self.db.execute('INSERT INTO publication_protocol VALUES(1)')
            self.owner_id = str(uuid.uuid4())
            self.closed = False
            self.lost = False
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
        need(not self.lost, 'collector lease lost')
        tick = time.monotonic()
        row = self.db.execute('SELECT owner,generation,live,heartbeat FROM lease WHERE id=1').fetchone()
        need(row is not None and row[:2] == (self.owner_id, self.generation), 'collector lease lost')
        wall = time.time()
        valid = row[2] == 1 and -CLOCK_TOLERANCE_SECONDS <= wall - row[3] < LEASE_SECONDS
        valid = valid and 0 <= tick - self.last_tick < LEASE_SECONDS
        if not valid:
            self.db.execute('UPDATE lease SET live=0 WHERE id=1')
            self.db.execute('COMMIT')
            self._publish()
            need(False, 'collector lease closed or expired')
        return wall, tick

    def _remove_public_journals(self):
        for suffix in ('-wal', '-shm', '-journal'):
            Path(self.path + suffix).unlink(missing_ok=True)

    def _temporary_prefix(self):
        return '.watch-' + uuid.uuid5(uuid.NAMESPACE_URL, self.path).hex + '-'

    def _clean_temporary_copies(self):
        for path in Path(self.path).parent.glob('.watch-*'):
            if not (path.name.startswith(self._temporary_prefix())
                    or re.fullmatch(r'\.watch-[a-z0-9_]{8}', path.name)):
                continue
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    # Other stores may publish in this directory concurrently.
                    # Never wait for their temporary inode; clean crash residue only.
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    current = os.fstat(fd)
                    if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                        path.unlink(missing_ok=True)
                finally:
                    os.close(fd)
            except (FileNotFoundError, BlockingIOError):
                continue

    def _publish(self):
        try:
            self._publish_snapshot()
        except BaseException:
            # A failed publication must not leave the previous live lease exposed.
            # If unlink also fails, propagate it and leave close retryable.
            if self.path is not None:
                Path(self.path).unlink(missing_ok=True)
            raise

    def _publish_snapshot(self):
        if self.path is None:
            return
        # Build on a new inode. Reader locks on any published inode can never
        # conflict with backup, checkpoint, fsync or replacement of this file.
        target = Path(self.path)
        info = target.stat() if target.exists() else None
        fd, temporary = tempfile.mkstemp(prefix=self._temporary_prefix(), dir=target.parent)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fchmod(fd, 0o600)
            with closing(sqlite3.connect(temporary, isolation_level=None)) as copy:
                self.db.backup(copy)
                copy.execute('PRAGMA journal_mode=DELETE')
            os.fsync(fd)
            if info is not None:
                os.fchown(fd, -1, info.st_gid)
                os.fchmod(fd, stat.S_IMODE(info.st_mode) & 0o777)
            # A newly provisioned projection starts private (0600). Grant the
            # reader group access explicitly, then replacements retain it.
            self._remove_public_journals()
            os.replace(temporary, target)
            directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            os.close(fd)
            Path(temporary).unlink(missing_ok=True)

    def _commit_checked(self, wall, tick):
        try:
            self.db.execute('COMMIT')
            self._publish()
            need(0 <= time.monotonic() - tick <= COMMIT_BOUND_SECONDS
                 and 0 <= time.time() - wall <= COMMIT_BOUND_SECONDS, 'collector lease lost')
        except BaseException:
            # Mark lost before cleanup: even a failed close cannot authorize this
            # object again. Wall elapsed also detects a forward clock step.
            self.lost = True
            try:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                self.db.execute('UPDATE lease SET live=0 WHERE id=1 AND owner=? AND generation=?',
                                (self.owner_id, self.generation))
                self._publish()
            finally:
                if self.path is not None:
                    Path(self.path).unlink(missing_ok=True)
            raise

    def _disconnect(self, prior):
        if prior:
            need(prior['scope'] == self.scope, 'watch store scope changed')
            prior['resync_required'] = True
        return prior

    @contextmanager
    def _write_guard(self):
        with _lease_lock(self.writer_path, writer=True):
            row = self.db.execute('SELECT owner,generation FROM lease WHERE id=1').fetchone()
            owns = row == (self.owner_id, self.generation)
            try:
                yield
            except BaseException:
                try:
                    if self.db.in_transaction:
                        self.db.execute('ROLLBACK')
                finally:
                    # Ownership was sampled under the same writer flock. No newer
                    # generation can publish before withdrawal finishes.
                    if owns:
                        self.lost = True
                        if self.path is not None:
                            Path(self.path).unlink(missing_ok=True)
                raise

    def heartbeat(self):
        with self._write_guard():
            self.db.execute('BEGIN IMMEDIATE')
            try:
                wall, tick = self._check_owner()
                self.db.execute('UPDATE lease SET heartbeat=? WHERE id=1', (wall,))
                if not 0 <= time.monotonic() - self.last_tick < LEASE_SECONDS:
                    self.db.execute('UPDATE lease SET live=0 WHERE id=1')
                    self.db.execute('COMMIT')
                    self._publish()
                    need(False, 'collector lease closed or expired')
                self._commit_checked(wall, tick)
                self.last_tick = tick
            except BaseException:
                if self.db.in_transaction: self.db.execute('ROLLBACK')
                raise

    def close(self):
        if self.closed: return
        with self._write_guard():
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute('UPDATE lease SET live=0 WHERE id=1 AND owner=? AND generation=?',
                                      (self.owner_id, self.generation)).rowcount
            self.db.execute('COMMIT')
            if changed and (self.path is None or Path(self.path).exists()):
                self._publish()
        self.closed = True
        self.db.close()

    def _transaction(self, update):
        with self._write_guard():
            self.db.execute('BEGIN IMMEDIATE')
            try:
                tick = time.monotonic()
                wall = time.time()
                if update != self._acquire:
                    wall, tick = self._check_owner()
                row = self.db.execute('SELECT body,digest FROM projection WHERE id=1').fetchone()
                prior = json.loads(row[0]) if row else None
                if row:
                    need(row[1] is None or row[1] == _digest(prior), 'watch integrity mismatch')
                    if row[1] is None: prior['resync_required'] = True
                value = update(prior)
                self.db.execute('UPDATE lease SET heartbeat=? WHERE id=1', (wall,))
                if value is not None:
                    bounded(value)
                    self.db.execute('INSERT OR REPLACE INTO projection VALUES(1,?,?)', (json.dumps(value, sort_keys=True), _digest(value)))
                self._commit_checked(wall, tick)
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


def _read_rows(path, *, integrity=False):
    # Sample before opening the snapshot, not after slow validation. Publication
    # can replace this inode while it is being read; its contents never change.
    before = time.time()
    need(sys.platform == 'linux', 'kernel publication evidence unavailable')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode), 'regular snapshot required')
        uri = 'file:/proc/self/fd/' + str(fd) + '?mode=ro&immutable=1'
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            db.execute('BEGIN')
            need(db.execute('SELECT version FROM publication_protocol').fetchall() == [(1,)],
                 'collector publication protocol required')
            if integrity:
                need(db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'watch integrity check failed')
            row = db.execute('SELECT body,digest FROM projection WHERE id=1').fetchone()
            lease = db.execute('SELECT owner,heartbeat,live,generation FROM lease WHERE id=1').fetchone()
            identity = db.execute('SELECT identity FROM store_identity WHERE id=1').fetchone()
            wall = time.time()
            db.execute('COMMIT')
        # SQLite may canonicalize the procfs link. With fresh-inode publication,
        # this comparison also excludes replacement during canonicalization/open.
        current_inode = os.stat(path, follow_symlinks=False)
        need((info.st_dev, info.st_ino) == (current_inode.st_dev, current_inode.st_ino),
             'watch changed during reader verification')
        published = os.fstat(fd).st_ctime
    finally:
        os.close(fd)
    return before, wall, row, lease, identity, published


def read_current(path, *, expiry_ledger, tenant, cluster, collection, namespace='', now):
    """Read the published snapshot; retain expiry in the reader's own ledger."""
    try:
        before, wall, row, lease, identity, published = _read_rows(path, integrity=True)
        need(row is not None, 'no topology collection')
        value = json.loads(row[0])
        need(row[1] == _digest(value), 'watch integrity mismatch')
        scope = [tenant, cluster, collection, namespace]
        need(value['scope'] == scope, 'watch scope mismatch')
        need(lease is not None and lease[0] and lease[3], 'collector lease missing')
        need(value.get('generation') == lease[3], 'projection generation mismatch')
        need(identity is not None and type(identity[0]) is str and identity[0], 'store identity missing')
        cutoff = LEASE_SECONDS + CLOCK_TOLERANCE_SECONDS + COMMIT_BOUND_SECONDS
        age = wall - lease[1]
        dead = lease[2] != 1 or before - lease[1] >= cutoff
        check_generation(expiry_ledger, identity[0], scope, lease[3], True)
        need(lease[2] != 1 or age >= -CLOCK_TOLERANCE_SECONDS,
             'collector heartbeat is in the future')
        need(lease[2] != 1 or published - lease[1] <= COMMIT_BOUND_SECONDS,
             'collector publication exceeded bound')
        if dead:
            check_generation(expiry_ledger, identity[0], scope, lease[3], False)
        need(lease[2] == 1 and age < LEASE_SECONDS, 'collector lease closed or expired')
        # Accept heartbeat-only progress, then derive every lease/ledger decision
        # again from that exact newer inode. Projection changes still refuse.
        before, wall, second_row, second_lease, second_identity, published = _read_rows(path)
        need(second_row == row and second_identity == identity
             and second_lease is not None and second_lease[0] == lease[0]
             and second_lease[3] == lease[3] and second_lease[1] >= lease[1],
             'watch changed during reader verification')
        lease = second_lease
        check_generation(expiry_ledger, identity[0], scope, lease[3], True)
        wall = time.time()
        need(lease[2] != 1 or wall - lease[1] >= -CLOCK_TOLERANCE_SECONDS,
             'collector heartbeat is in the future')
        need(lease[2] != 1 or published - lease[1] <= COMMIT_BOUND_SECONDS, 'collector publication exceeded bound')
        age = wall - lease[1]
        if lease[2] != 1 or before - lease[1] >= cutoff:
            check_generation(expiry_ledger, identity[0], scope, lease[3], False)
        need(age >= -CLOCK_TOLERANCE_SECONDS, 'collector heartbeat is in the future')
        need(lease[2] == 1 and age < LEASE_SECONDS, 'collector lease closed or expired')
        need(abs(_utc(now).timestamp() - wall) <= CLOCK_TOLERANCE_SECONDS, 'reader clock differs from wall clock')
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
