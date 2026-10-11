import os
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError


@pytest.mark.parametrize('failure', ['absent', 'denied', 'missing_id'])
def test_startup_procfs_refusal_preserves_running_lease(tmp_path, monkeypatch, failure):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    before = path.read_bytes(), path.stat().st_ino
    original = Path.read_text
    def unavailable(path, *args, **kwargs):
        if str(path).startswith('/proc/self/fdinfo/'):
            if failure == 'absent': raise FileNotFoundError('missing procfs')
            if failure == 'denied': raise PermissionError('denied procfs')
            return 'pos: 0\n'
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(w.sys, 'platform', 'linux')
        patch.setattr(Path, 'read_text', unavailable)
        with pytest.raises(ObservationError, match='procfs mount identity access required'):
            w.WatchStore(path, 't', 'c', 'nodes')
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert store.db.execute('SELECT owner,live FROM lease').fetchone() == (store.owner_id, 1)
    store.heartbeat()
    store.close()


def test_startup_with_hidden_procfs_preserves_running_lease(tmp_path, record_property):
    import shutil
    import subprocess
    import sys
    available = sys.platform == 'linux' and shutil.which('unshare')
    if available:
        available = subprocess.run(['unshare', '-Urm', 'true'], capture_output=True, timeout=10).returncode == 0
    if not available:
        record_property('procfs_validation', 'injected; namespace unavailable')
        with pytest.MonkeyPatch.context() as patch:
            test_startup_procfs_refusal_preserves_running_lease(tmp_path, patch, 'absent')
        return
    record_property('procfs_validation', 'real hidden procfs')
    child = """
from pathlib import Path
import subprocess, sys
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import ObservationError
path = Path(sys.argv[1]) / 'db'
s = WatchStore(path, 't', 'c', 'nodes')
before = path.read_bytes(), path.stat().st_ino
def mount(*args): subprocess.run(args, check=True, timeout=5)
mount('mount', '-t', 'tmpfs', 'tmpfs', '/proc')
try:
    try: WatchStore(path, 't', 'c', 'nodes')
    except ObservationError as error:
        assert 'procfs mount identity access required' in str(error), error
    else: raise AssertionError('startup without procfs accepted')
    assert (path.read_bytes(), path.stat().st_ino) == before
    assert s.db.execute('SELECT owner,live FROM lease').fetchone() == (s.owner_id, 1)
finally: mount('umount', '/proc')
s.heartbeat(); s.close()
"""
    result = subprocess.run(['unshare', '-Urm', sys.executable, '-c', child, str(tmp_path)],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
