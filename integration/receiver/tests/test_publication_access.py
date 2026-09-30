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
    path.chmod(mode)
    group = path.stat().st_gid
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
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
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
