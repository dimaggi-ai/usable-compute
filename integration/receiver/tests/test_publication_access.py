import fcntl
import os
import stat
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read


@pytest.mark.parametrize('mode', [0o600, 0o640, 0o660])
def test_publication_strips_write_bits_and_restores_access(published, monkeypatch, mode):
    store, path, ledger = published
    store.close()
    group = path.stat().st_gid
    store = w.WatchStore(path, 't', 'c', 'nodes', publication_mode=mode, publication_gid=group)
    path.chmod(0o400)
    store.heartbeat()
    assert stat.S_IMODE(path.stat().st_mode) == mode & ~0o222
    with monkeypatch.context() as patch:
        original = os.replace
        def fail(src, dst):
            if Path(dst) == path: raise OSError('injected replacement failure')
            return original(src, dst)
        patch.setattr(os, 'replace', fail)
        with pytest.raises(OSError): store.heartbeat()
    assert not path.exists()
    store.close()
    replacement = w.WatchStore(path, 't', 'c', 'nodes', publication_mode=mode, publication_gid=group)
    try:
        assert (stat.S_IMODE(path.stat().st_mode), path.stat().st_gid) == (mode & ~0o222, group)
    finally:
        replacement.close()


def test_acquisition_refuses_active_legacy_writer(published):
    store, path, _ = published
    lock = Path(str(path)+'.lease-lock')
    lock.touch()
    before = path.read_bytes()
    with lock.open('rb') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match='legacy collector'):
            w.WatchStore(path, 't', 'c', 'nodes')
        assert path.read_bytes() == before
        assert lock.exists()
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    replacement.close()


@pytest.mark.parametrize('withdrawal', ['begin', 'publish'])
def test_revoked_observed_grant_is_not_restored(published, monkeypatch, withdrawal):
    import sqlite3
    store, path, ledger = published
    # Simulate an existing installation with a previously learned group grant.
    store.db.execute('CREATE TABLE IF NOT EXISTS publication_access (id INTEGER PRIMARY KEY, mode INTEGER, gid INTEGER)')
    store.db.execute('INSERT OR REPLACE INTO publication_access VALUES(1,?,?)', (0o440, os.getegid()))
    path.chmod(0o440)
    store.heartbeat()
    path.chmod(0o400)
    with monkeypatch.context() as patch:
        if withdrawal == 'begin':
            from test_failure_ownership import Fault
            patch.setattr(store, 'db', Fault(store.db, lambda sql: sql == 'BEGIN IMMEDIATE'))
        else:
            patch.setattr(store, '_publish_snapshot', lambda: (_ for _ in ()).throw(OSError('injected failure')))
        with pytest.raises((sqlite3.Error, OSError)): store.heartbeat()
    assert not path.exists()
    store.close()
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
        replacement.heartbeat()
        assert stat.S_IMODE(path.stat().st_mode) == 0o400
    finally:
        replacement.close()
