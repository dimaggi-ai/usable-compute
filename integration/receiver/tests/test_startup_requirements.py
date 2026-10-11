import os
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError


@pytest.mark.parametrize('existing', [False, True])
def test_access_refusal_precedes_any_startup_write(tmp_path, monkeypatch, existing):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes') if existing else None
    before = (path.read_bytes(), path.stat().st_ino) if existing else None
    def readonly(*args, **kwargs):
        raise OSError(30, 'Read-only file system')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'access', lambda *a, **k: False)
        patch.setattr(Path, 'mkdir', readonly)
        patch.setattr(os, 'fchmod', readonly)
        with pytest.raises(ObservationError, match='publication directory requires write and search access'):
            w.WatchStore(path, 't', 'c', 'nodes')
    if store:
        assert (path.read_bytes(), path.stat().st_ino) == before
        assert store.db.execute('SELECT owner,live FROM lease').fetchone() == (store.owner_id, 1)
        store.heartbeat()
        store.close()
    else:
        assert list(tmp_path.iterdir()) == []



@pytest.mark.parametrize('existing', [False, True])
def test_readonly_parent_and_private_mount_refuse(tmp_path, existing, record_property):
    import shutil
    import subprocess
    import sys
    available = sys.platform == 'linux' and shutil.which('unshare')
    if available:
        available = subprocess.run(['unshare', '-Urm', 'true'], capture_output=True, timeout=10).returncode == 0
    if not available:
        record_property('access_validation', 'injected; namespace unavailable')
        with pytest.MonkeyPatch.context() as patch:
            test_access_refusal_precedes_any_startup_write(tmp_path, patch, existing)
        return
    record_property('access_validation', 'real readonly parent and private mount')
    child = """
from pathlib import Path
import subprocess, sys
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import ObservationError
root = Path(sys.argv[1]); path = root / 'db'
s = WatchStore(path, 't', 'c', 'nodes') if sys.argv[2] == 'True' else None
before = (path.read_bytes(), path.stat().st_ino) if s else None
def mount(*args): subprocess.run(args, check=True, timeout=5)
mount('mount', '--bind', str(root), str(root))
mount('mount', '-o', 'remount,bind,ro', str(root))
try:
    try: WatchStore(path, 't', 'c', 'nodes')
    except ObservationError as error:
        assert 'publication directory requires write and search access' in str(error), error
    else: raise AssertionError('readonly startup accepted')
    if s:
        assert (path.read_bytes(), path.stat().st_ino) == before
        assert s.db.execute('SELECT owner,live FROM lease').fetchone() == (s.owner_id, 1)
    else: assert list(root.iterdir()) == []
finally: mount('umount', str(root))
if s: s.heartbeat(); s.close()
"""
    result = subprocess.run(['unshare', '-Urm', sys.executable, '-c', child, str(tmp_path), str(existing)],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
