import errno
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError
from test_publication_evidence import published, read


@pytest.mark.parametrize('operation', ['close', 'fail', 'heartbeat', 'retire', 'projection'])
@pytest.mark.parametrize('error', [errno.ENOSPC, errno.EACCES, errno.EXDEV])
def test_failed_publication_withdraws_current(published, monkeypatch, operation, error):
    store, path, ledger = published
    before = ledger.read_bytes()
    def fail(*args): raise OSError(error, 'injected replacement failure')
    with monkeypatch.context() as patch:
        patch.setattr(w.os, 'replace', fail)
        if operation == 'retire': store.last_tick -= 46
        expected = ObservationError if error == errno.EXDEV else OSError
        message = 'same filesystem and mount' if error == errno.EXDEV else 'injected replacement failure'
        with pytest.raises(expected, match=message):
            if operation == 'projection': store._transaction(lambda value: value)
            elif operation == 'retire': store.heartbeat()
            else: getattr(store, operation)()
        with pytest.raises(ValueError): read(path, ledger)
        assert not path.exists()
        assert ledger.read_bytes() == before
        if operation == 'close':
            store.close()
            assert store.closed


def test_close_retries_when_publication_and_withdrawal_fail(published, monkeypatch):
    store, path, ledger = published
    original = Path.unlink
    def fail(*args): raise OSError(errno.EACCES, 'injected denied replacement')
    def unlink(p, *args, **kwargs):
        if p == path: raise OSError(errno.EACCES, 'injected denied unlink')
        return original(p, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(w.os, 'replace', fail)
        patch.setattr(Path, 'unlink', unlink)
        for _ in range(2):
            with pytest.raises(OSError): store.close()
            assert not store.closed
            assert store.db.execute('SELECT live FROM lease').fetchone() == (0,)
    store.close()
    assert store.closed
    with pytest.raises(ValueError): read(path, ledger)


@pytest.mark.parametrize('operation', ['heartbeat', 'close', 'fail', 'retire'])
@pytest.mark.parametrize('failure', ['commit', 'update', 'both'])
def test_private_write_failure_withdraws_snapshot(published, monkeypatch, operation, failure):
    import sqlite3
    store, path, ledger = published
    real = store.db
    class Broken:
        def __getattr__(self, name): return getattr(real, name)
        def execute(self, sql, *args):
            if ((failure in ('commit', 'both') and sql == 'COMMIT')
                    or (failure in ('update', 'both') and sql.startswith('UPDATE lease SET live=0'))):
                raise sqlite3.OperationalError('injected private write failure')
            return real.execute(sql, *args)
    # Force the fallback for an update-only failure after a completed commit.
    with monkeypatch.context() as patch:
        patch.setattr(store, 'db', Broken())
        if failure == 'update' and operation in ('heartbeat', 'fail'):
            patch.setattr(store, '_publish', lambda: (_ for _ in ()).throw(OSError('injected publication failure')))
        if operation == 'retire': store.last_tick -= 46
        with pytest.raises((sqlite3.Error, OSError, ValueError)):
            getattr(store, 'heartbeat' if operation == 'retire' else operation)()
        before = ledger.read_bytes()
        if failure == 'commit' and operation in ('heartbeat', 'fail'):
            assert path.exists()
            with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
            assert len(ledger.read_text().splitlines()) == 2
        else:
            assert not path.exists()
            with pytest.raises(ValueError): read(path, ledger)
            assert ledger.read_bytes() == before
        if operation == 'close':
            assert not store.closed
            with pytest.raises(sqlite3.Error): store.close()
    store.close()
    assert store.closed


def test_failed_old_close_cannot_unlink_new_generation(published, monkeypatch):
    import sqlite3
    store, path, ledger = published
    newer = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        original = store.db
        class Broken:
            def __getattr__(self, name): return getattr(original, name)
            def execute(self, sql, *args):
                if sql.startswith('UPDATE lease'): raise sqlite3.OperationalError('injected failure')
                return original.execute(sql, *args)
        with monkeypatch.context() as patch:
            patch.setattr(store, 'db', Broken())
            before = path.read_bytes()
            with pytest.raises(sqlite3.Error): store.close()
            assert path.read_bytes() == before
    finally:
        newer.close()
