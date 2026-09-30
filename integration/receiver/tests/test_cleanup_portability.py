import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from dimaggi_receiver import topology_watch as w


@pytest.mark.parametrize('mode', [0o40, 0, 0o4])
def test_restart_without_nofollow_chmod(tmp_path, monkeypatch, mode):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    residue = Path(store.writer_path).parent / (store._temporary_prefix() + 'crash')
    residue.touch(mode=mode)
    chmod = os.chmod
    def unsupported(path, mode, *args, **kwargs):
        if kwargs.get('follow_symlinks') is False:
            raise NotImplementedError('no-follow chmod unavailable')
        return chmod(path, mode, *args, **kwargs)
    monkeypatch.setattr(os, 'chmod', unsupported)
    if hasattr(os, 'O_PATH'):
        replacement = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
        assert not residue.exists()
    else:
        with pytest.warns(RuntimeWarning, match='temporary cleanup failed'):
            replacement = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
        assert residue.exists()
    replacement.close()
    store.close()


@pytest.mark.parametrize('mode', [0o40, 0])
def test_crash_restart_with_unsupported_libc_chmod(tmp_path, mode, record_property):
    if sys.platform != 'linux' or not shutil.which('cc'):
        record_property('chmod_validation', 'libc shim unavailable; injected case covers fallback')
        with pytest.MonkeyPatch.context() as patch:
            test_restart_without_nofollow_chmod(tmp_path, patch, mode)
        return
    source = tmp_path / 'shim.c'
    source.write_text('''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
int fchmodat(int dirfd, const char *path, mode_t mode, int flags) {
    if (flags & AT_SYMLINK_NOFOLLOW) { errno = ENOTSUP; return -1; }
    int (*next)(int, const char *, mode_t, int) = dlsym(RTLD_NEXT, "fchmodat");
    return next(dirfd, path, mode, flags);
}
''')
    shim = tmp_path / 'shim.so'
    subprocess.run(['cc', '-shared', '-fPIC', str(source), '-o', str(shim), '-ldl'],
                   check=True, capture_output=True, timeout=15)
    path = tmp_path / 'db'
    child = '''
import os, sys
from pathlib import Path
from dimaggi_receiver.topology_watch import WatchStore
path = Path(sys.argv[1])
s = WatchStore(path, 't', 'c', 'nodes', publication_mode=int(sys.argv[2]))
replace = os.replace
def crash(source, target):
    if Path(target) == path: os._exit(77)
    replace(source, target)
os.replace = crash
s.heartbeat()
'''
    result = subprocess.run([sys.executable, '-c', child, str(path), str(mode)], timeout=15)
    assert result.returncode == 77
    residue = list(Path(str(path) + '.collector').glob('.watch-*'))
    assert len(residue) == 1 and residue[0].stat().st_mode & 0o777 == mode
    restart = '''
import sys
from dimaggi_receiver.topology_watch import WatchStore
s = WatchStore(sys.argv[1], 't', 'c', 'nodes'); s.close()
'''
    result = subprocess.run([sys.executable, '-c', restart, str(path)],
                            env=dict(os.environ, LD_PRELOAD=str(shim)),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not residue[0].exists()


def test_cleanup_descriptor_rejects_symlink_substitution(tmp_path, monkeypatch):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    residue = Path(store.writer_path).parent / (store._temporary_prefix() + 'swap')
    residue.touch(mode=0)
    foreign = tmp_path / 'foreign'
    foreign.write_text('preserve')
    foreign.chmod(0)
    original = os.open
    def swapped(path, flags, *args, **kwargs):
        if Path(path) == residue:
            residue.unlink()
            residue.symlink_to(foreign)
        return original(path, flags, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'open', swapped)
        store._clean_temporary_copies()
    assert foreign.stat().st_mode & 0o777 == 0
    assert residue.is_symlink()
    store.close()


@pytest.mark.parametrize('error', [NotImplementedError, ValueError, OSError])
def test_cleanup_entry_error_warns_and_continues(tmp_path, monkeypatch, error):
    store = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    residue = Path(store.writer_path).parent / (store._temporary_prefix() + 'denied')
    residue.touch(mode=0)
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path == residue: raise error('entry unavailable')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', denied)
        with pytest.warns(RuntimeWarning, match='temporary cleanup failed'):
            replacement = w.WatchStore(tmp_path / 'db', 't', 'c', 'nodes')
    assert residue.exists()
    replacement.close()
    store.close()
