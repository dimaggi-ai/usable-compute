import os
import sqlite3
import time

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read, iso


class Fault:
    def __init__(self, db, predicate):
        self.db, self.predicate = db, predicate
    def __getattr__(self, key): return getattr(self.db, key)
    def execute(self, sql, *args):
        if self.predicate(sql): raise sqlite3.OperationalError('injected storage failure')
        return self.db.execute(sql, *args)


@pytest.mark.parametrize('operation', ['heartbeat', 'close', 'fail'])
@pytest.mark.parametrize('obsolete', [False, True])
def test_ownership_query_failure_is_generation_safe(published, monkeypatch, operation, obsolete):
    store, path, ledger = published
    newer = w.WatchStore(path, 't', 'c', 'nodes') if obsolete else None
    before = path.read_bytes()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, 'db', Fault(store.db, lambda sql: sql.startswith('SELECT owner')))
            with pytest.raises(sqlite3.Error): getattr(store, operation)()
        if obsolete:
            assert path.read_bytes() == before
        else:
            assert not path.exists()
            with pytest.raises(ValueError): read(path, ledger)
    finally:
        if newer: newer.close()


@pytest.mark.parametrize('closed', [False, True])
def test_acquisition_commit_failure_preserves_prior_snapshot(published, monkeypatch, closed):
    store, path, ledger = published
    if closed: store.close()
    before, inode = path.read_bytes(), path.stat().st_ino
    connect = w.sqlite3.connect
    class Broken(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == 'COMMIT': raise sqlite3.OperationalError('injected acquisition failure')
            return super().execute(sql, *args)
    with monkeypatch.context() as patch:
        patch.setattr(w.sqlite3, 'connect', lambda *a, **k: connect(*a, **dict(k, factory=Broken)))
        with pytest.raises(sqlite3.Error): w.WatchStore(path, 't', 'c', 'nodes')
    assert path.exists()
    assert (path.read_bytes(), path.stat().st_ino) == (before, inode)
    if closed:
        with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
        assert len(ledger.read_text().splitlines()) == 2
    else:
        assert not read(path, ledger)['issues']
        store.heartbeat()


@pytest.mark.parametrize('failure', ['commit', 'late'])
def test_successful_retirement_retains_death_across_restore(published, monkeypatch, failure):
    store, path, ledger = published
    old = path.read_bytes()
    backup = path.parent/'private-backup.db'
    with sqlite3.connect(backup) as db: store.db.backup(db)
    with monkeypatch.context() as patch:
        if failure == 'commit':
            patch.setattr(store, 'db', Fault(store.db, lambda sql: sql == 'COMMIT'))
        else:
            # A real delayed heartbeat, with a shortened publication budget.
            patch.setattr(w, 'COMMIT_BOUND_SECONDS', 0.05)
            original = store._publish
            def delayed():
                original()
                time.sleep(0.08)
            patch.setattr(store, '_publish', delayed)
        with pytest.raises((sqlite3.Error, ValueError)): store.heartbeat()
    assert path.exists()
    assert store.db.execute('SELECT live FROM lease').fetchone() == (0,)
    with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
    assert len(ledger.read_text().splitlines()) == 2
    store.close()
    from pathlib import Path
    Path(store.writer_path).write_bytes(backup.read_bytes())
    restored = path.parent/'restored'
    restored.write_bytes(old)
    os.replace(restored, path)
    with pytest.raises(ValueError, match='previously expired'): read(path, ledger)


@pytest.mark.parametrize('corruption', ['digest', 'json'])
def test_failed_invalidation_withdraws_on_private_integrity_error(published, corruption):
    store, path, ledger = published
    if corruption == 'digest': store.db.execute("UPDATE projection SET digest='broken'")
    else: store.db.execute("UPDATE projection SET body='{' ")
    with pytest.raises(ValueError): store.fail()
    assert not path.exists()
    with pytest.raises(ValueError): read(path, ledger)


@pytest.mark.parametrize('event_type', ['ADDED', 'BOOKMARK'])
@pytest.mark.parametrize('metadata', [[], ['bad'], None, 3])
def test_malformed_metadata_invalidates_and_allows_relist(published, event_type, metadata):
    store, path, ledger = published
    now = time.time()
    with pytest.raises(ValueError):
        store.apply([{'type': event_type, 'object': {'apiVersion': 'v1', 'kind': 'Node',
                     'metadata': metadata}}], iso(now), iso(now+290))
    assert not store.lost
    with pytest.raises(ValueError, match='resync_required'): read(path, ledger)
    store.relist({'apiVersion': 'v1', 'kind': 'NodeList',
                  'metadata': {'resourceVersion': '2'}, 'items': []}, iso(now), iso(now+290))
    assert not read(path, ledger)['issues']
