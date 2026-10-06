import errno
import os
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError
from test_publication_evidence import published, read


@pytest.mark.parametrize('prior', [False, True])
def test_startup_cross_mount_refusal_preserves_prior(tmp_path, monkeypatch, prior):
    path = tmp_path / 'db'
    if prior:
        store = w.WatchStore(path, 't', 'c', 'nodes')
        before = path.read_bytes(), path.stat().st_ino
    def replace(source, target):
        raise OSError(errno.EXDEV, 'cross-device link')
    monkeypatch.setattr(os, 'replace', replace)
    with pytest.raises(ObservationError, match='same filesystem and mount'):
        w.WatchStore(path, 't', 'c', 'nodes')
    assert not list(tmp_path.rglob('.watch-*'))
    if prior:
        assert (path.read_bytes(), path.stat().st_ino) == before
        monkeypatch.undo()
        store.close()
    else:
        assert not path.exists()
        assert not (tmp_path / 'db.collector' / 'writer.db').exists()


def test_real_publication_cross_mount_refuses_and_withdraws(published, monkeypatch):
    store, path, ledger = published
    replace = os.replace
    def fault(source, target):
        if Path(target) == path: raise OSError(errno.EXDEV, 'mount changed')
        return replace(source, target)
    monkeypatch.setattr(os, 'replace', fault)
    with pytest.raises(ObservationError, match='same filesystem and mount'):
        store.heartbeat()
    assert not path.exists() and store._published_fd is None
    assert not list(Path(store.writer_path).parent.glob('.watch-*'))


@pytest.mark.parametrize('kind', ['tmpfs', 'bind'])
def test_real_mount_placement_refuses_before_acquisition(tmp_path, kind, record_property):
    import shutil
    import subprocess
    import sys
    available = sys.platform == 'linux' and shutil.which('unshare')
    if available:
        capability = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount', 'true'],
                                    capture_output=True, timeout=10)
        available = capability.returncode == 0
    if not available:
        from unittest.mock import patch
        record_property('mount_validation', 'injected EXDEV; mount namespace unavailable')
        with patch.object(os, 'replace', side_effect=OSError(errno.EXDEV, 'cross-device link')):
            with pytest.raises(ObservationError, match='same filesystem and mount'):
                w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
        assert not (tmp_path / 'db').exists()
        assert not (tmp_path / 'db.collector' / 'writer.db').exists()
        assert not list(tmp_path.rglob('.watch-*'))
        return
    record_property('mount_validation', 'real namespace ' + kind)
    child = '''
import os, subprocess, sys
from pathlib import Path
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import ObservationError
root = Path(sys.argv[1])
private = root / 'db.collector'
private.mkdir(mode=0o700)
source = root / 'backing'
source.mkdir(mode=0o700)
command = ['mount', '--bind', str(source), str(private)] if sys.argv[2] == 'bind' else [
    'mount', '-t', 'tmpfs', '-o', 'mode=0700', 'tmpfs', str(private)]
subprocess.run(command, check=True, timeout=5)
try:
    assert (private.stat().st_dev == root.stat().st_dev) == (sys.argv[2] == 'bind')
    try:
        WatchStore(root / 'db', 't', 'c', 'nodes')
    except ObservationError as error:
        assert 'same filesystem and mount' in str(error)
    else:
        raise AssertionError('cross-mount placement accepted')
    assert not (root / 'db').exists()
    assert not (private / 'writer.db').exists()
    assert not list(root.rglob('.watch-*'))
finally:
    subprocess.run(['umount', str(private)], check=True, timeout=5)
'''
    result = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount',
                             sys.executable, '-c', child, str(tmp_path), kind],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
