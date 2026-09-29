import errno
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
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
        with pytest.raises(OSError):
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
