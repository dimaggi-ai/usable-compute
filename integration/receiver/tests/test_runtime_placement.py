from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError


@pytest.mark.parametrize('failure', ['private', 'procfs'])
@pytest.mark.parametrize('successor', [False, True])
def test_runtime_placement_failure_is_owned_and_explicit(tmp_path, monkeypatch, failure, successor):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    next_store = w.WatchStore(path, 't', 'c', 'nodes') if successor else None
    before = path.read_bytes(), path.stat().st_ino
    private = Path(store.writer_path).parent
    hidden = tmp_path / 'hidden'
    if failure == 'private':
        private.rename(hidden)
        private.mkdir(mode=0o700)
    else:
        original = Path.read_text
        def unavailable(path, *args, **kwargs):
            if str(path).startswith('/proc/self/fdinfo/'):
                raise FileNotFoundError('procfs unavailable')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'read_text', unavailable)
    try:
        with pytest.raises(ObservationError, match='collector publication placement unavailable'):
            store.heartbeat()
        assert store.lost and store._published_fd is None
        if successor:
            assert (path.read_bytes(), path.stat().st_ino) == before
        else:
            assert not path.exists()
    finally:
        monkeypatch.undo()
        if failure == 'private':
            private.rmdir()
            hidden.rename(private)
        store.close()
        if next_store:
            next_store.heartbeat()
            next_store.close()


@pytest.mark.parametrize('failure', ['private', 'procfs'])
def test_runtime_namespace_placement_change(tmp_path, failure, record_property):
    import shutil
    import subprocess
    import sys
    available = sys.platform == 'linux' and shutil.which('unshare')
    if available:
        available = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount', 'true'],
                                   capture_output=True, timeout=10).returncode == 0
    if not available:
        record_property('placement_validation', 'injected; namespace unavailable')
        with pytest.MonkeyPatch.context() as patch:
            test_runtime_placement_failure_is_owned_and_explicit(tmp_path, patch, failure, False)
        return
    record_property('placement_validation', 'real namespace ' + failure)
    child = '''
from pathlib import Path
import subprocess, sys
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import ObservationError
root = Path(sys.argv[1]); path = root / 'db'
s = WatchStore(path, 't', 'c', 'nodes')
target = str(Path(s.writer_path).parent) if sys.argv[2] == 'private' else '/proc'
subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=0700', 'tmpfs', target], check=True, timeout=5)
try:
    try: s.heartbeat()
    except ObservationError as error:
        assert 'collector publication placement unavailable' in str(error), error
    else: raise AssertionError('placement change accepted')
    assert s.lost and s._published_fd is None and not path.exists()
finally:
    subprocess.run(['umount', target], check=True, timeout=5)
s.close()
'''
    result = subprocess.run(['unshare', '--map-auto', '--map-root-user', '--mount',
                             sys.executable, '-c', child, str(tmp_path), failure],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr


def test_unavailable_writer_lock_refuses_without_unsynchronized_unlink(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    before = (tmp_path / 'db').read_bytes()
    @contextmanager
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('lock unavailable')
        yield
    with monkeypatch.context() as patch:
        patch.setattr(w, '_lease_lock', unavailable)
        with pytest.raises(ObservationError, match='collector lease lock unavailable'):
            store.heartbeat()
    assert store.lost and store._published_fd is None
    assert (tmp_path / 'db').read_bytes() == before
    store.close()
