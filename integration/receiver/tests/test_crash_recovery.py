import errno
import fcntl
import os
from pathlib import Path
import subprocess
import sys

import pytest
from dimaggi_receiver import topology_watch as w


@pytest.mark.parametrize('mode', [0o40, 0, 0o4])
@pytest.mark.parametrize('defaults', [False, True])
def test_unreadable_crash_copy_restart(tmp_path, mode, defaults):
    path = tmp_path / 'db'
    child = '''
import os, sys
from pathlib import Path
from dimaggi_receiver.topology_watch import WatchStore
path = Path(sys.argv[1])
s = WatchStore(path, 't', 'c', 'nodes', publication_mode=int(sys.argv[2]))
real = os.replace
def crash(source, target):
    if Path(target) == path: os._exit(77)
    real(source, target)
os.replace = crash
s.heartbeat()
'''
    result = subprocess.run([sys.executable, '-c', child, str(path), str(mode)], timeout=10)
    assert result.returncode == 77
    residue = list(Path(str(path) + '.collector').glob('.watch-*'))
    assert len(residue) == 1
    assert residue[0].stat().st_mode & 0o777 == mode
    options = {} if defaults else {'publication_mode': mode}
    store = w.WatchStore(path, 't', 'c', 'nodes', **options)
    store.close()
    assert not residue[0].exists()


def test_unreadable_active_and_odd_entries_survive(tmp_path):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    private = Path(store.writer_path).parent
    prefix = store._temporary_prefix()
    active = private / (prefix + 'active')
    active.touch()
    external = tmp_path / 'external'
    external.write_text('preserve')
    link = private / (prefix + 'link')
    link.symlink_to(external)
    hardlink = private / (prefix + 'hardlink')
    os.link(external, hardlink)
    fifo = private / (prefix + 'fifo')
    os.mkfifo(fifo)
    directory = private / (prefix + 'directory')
    directory.mkdir()
    with active.open('rb') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        active.chmod(0)
        replacement = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
        replacement.close()
        assert active.exists() and active.stat().st_mode & 0o777 == 0
    assert link.is_symlink() and hardlink.exists() and fifo.exists() and directory.is_dir()
    assert external.read_text() == 'preserve'
    store.close()


def test_cleanup_error_is_visible_and_does_not_block_restart(tmp_path, monkeypatch):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    residue = Path(store.writer_path).parent / (store._temporary_prefix() + 'blocked')
    residue.touch()
    real = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == residue: raise PermissionError(errno.EACCES, 'cleanup denied')
        return real(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    with pytest.warns(RuntimeWarning, match='temporary cleanup failed'):
        replacement = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    replacement.close()
    store.close()
