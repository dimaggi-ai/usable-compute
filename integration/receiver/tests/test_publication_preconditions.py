import os
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError


@pytest.mark.parametrize('mode', [0o555, 0o500])
def test_unwritable_public_directory_preserves_running_lease(tmp_path, mode):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    before = path.read_bytes(), path.stat().st_ino
    tmp_path.chmod(mode)
    try:
        with pytest.raises(ObservationError, match='publication directory requires write and search access'):
            w.WatchStore(path, 't', 'c', 'nodes')
        assert store.db.execute('SELECT owner,live FROM lease').fetchone() == (store.owner_id, 1)
        assert (path.read_bytes(), path.stat().st_ino) == before
        assert sorted(p.name for p in tmp_path.iterdir()) == ['db', 'db.collector']
    finally:
        tmp_path.chmod(0o700)
    store.heartbeat()
    store.close()


def test_public_access_uses_effective_credentials_before_acquisition(tmp_path, monkeypatch):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    before = (tmp_path / 'db').stat().st_ino
    calls = []
    def access(path, mode, *, effective_ids=False):
        calls.append((Path(path), mode, effective_ids))
        return False
    with monkeypatch.context() as patch:
        patch.setattr(os, 'access', access)
        with pytest.raises(ObservationError, match='publication directory requires write and search access'):
            w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    assert calls == [(tmp_path, os.W_OK | os.X_OK, True)]
    assert (tmp_path / 'db').stat().st_ino == before
    assert store.db.execute('SELECT owner,live FROM lease').fetchone() == (store.owner_id, 1)
    store.heartbeat()
    store.close()


def test_readonly_mount_refuses_before_acquisition(tmp_path, record_property):
    import shutil
    import subprocess
    import sys
    available = sys.platform == 'linux' and shutil.which('unshare')
    if available:
        available = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount', 'true'],
                                   capture_output=True, timeout=10).returncode == 0
    if not available:
        record_property('access_validation', 'injected; namespace unavailable')
        with pytest.MonkeyPatch.context() as patch:
            test_public_access_uses_effective_credentials_before_acquisition(tmp_path, patch)
        return
    record_property('access_validation', 'real readonly mount')
    child = '''
from pathlib import Path
import subprocess, sys
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import ObservationError
root = Path(sys.argv[1]); path = root / 'db'
s = WatchStore(path, 't', 'c', 'nodes')
before = path.read_bytes(), path.stat().st_ino
# Keep the private child mount writable while making the public mount readonly.
for p in (root, Path(s.writer_path).parent):
    subprocess.run(['mount', '--bind', str(p), str(p)], check=True, timeout=5)
subprocess.run(['mount', '-o', 'remount,bind,ro', str(root)], check=True, timeout=5)
try:
    try: WatchStore(path, 't', 'c', 'nodes')
    except ObservationError as error:
        assert 'publication directory requires write and search access' in str(error), error
    else: raise AssertionError('readonly publication accepted')
    assert s.db.execute('SELECT owner,live FROM lease').fetchone() == (s.owner_id, 1)
    assert (path.read_bytes(), path.stat().st_ino) == before
finally:
    subprocess.run(['umount', str(Path(s.writer_path).parent)], check=True, timeout=5)
    subprocess.run(['umount', str(root)], check=True, timeout=5)
s.heartbeat(); s.close()
'''
    result = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount',
                             sys.executable, '-c', child, str(tmp_path)],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_sticky_public_directory_refuses_foreign_target_before_acquisition(tmp_path, monkeypatch):
    import stat
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    before = path.read_bytes(), path.stat().st_ino
    original = os.stat
    def foreign_owner(target, *args, **kwargs):
        result = original(target, *args, **kwargs)
        if isinstance(target, (str, bytes, os.PathLike)) and Path(target) in (tmp_path, path):
            values = list(result)
            values[4] = os.geteuid() + 1
            if Path(target) == tmp_path:
                values[0] |= stat.S_ISVTX
            return os.stat_result(values)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(os, 'stat', foreign_owner)
        with pytest.raises(ObservationError, match='sticky directory'):
            w.WatchStore(path, 't', 'c', 'nodes')
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert store.db.execute('SELECT owner,live FROM lease').fetchone() == (store.owner_id, 1)
    store.heartbeat()
    store.close()
