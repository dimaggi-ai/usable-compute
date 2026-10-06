from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from dimaggi_receiver import topology_watch as w


def test_publication_with_interacting_sqlite_and_flock_locks(tmp_path):
    child = '''
from contextlib import closing
from pathlib import Path
import fcntl, os, sqlite3, struct, sys
from dimaggi_receiver.topology_watch import WatchStore
connect = sqlite3.connect
if sys.platform == 'linux':
    # OFD locks conflict with SQLite's POSIX locks, as flock does on Darwin.
    original = fcntl.flock
    def interacting(fd, operation):
        name = Path(os.readlink(f'/proc/self/fd/{fd}')).name
        writable = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR
        if not name.startswith('.watch-') or not writable:
            return original(fd, operation)
        kind = fcntl.F_UNLCK if operation & fcntl.LOCK_UN else fcntl.F_WRLCK
        return fcntl.fcntl(fd, 37, struct.pack('@hhqqi4x', kind, os.SEEK_SET, 0, 0, 0))
    fcntl.flock = interacting
path = Path(sys.argv[1])
store = WatchStore(path, 't', 'c', 'nodes')
try:
    store.heartbeat()
    with closing(connect(path)) as snapshot:
        assert snapshot.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert snapshot.execute('SELECT owner,generation,live FROM lease').fetchone() == (store.owner_id, store.generation, 1)
    replacement = WatchStore(path, 't', 'c', 'nodes')
    replacement.close()
finally:
    store.close()
assert not list(Path(str(path) + '.collector').glob('.watch-*'))
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path/'db')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_busy_backup_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(w, 'BACKUP_TIMEOUT_SECONDS', 0.05)
    source = sqlite3.connect(':memory:')
    target = sqlite3.connect(tmp_path/'copy', timeout=0)
    blocker = sqlite3.connect(tmp_path/'copy', timeout=0)
    try:
        source.execute('CREATE TABLE example (id INTEGER)')
        blocker.execute('BEGIN EXCLUSIVE')
        start = time.perf_counter()
        with pytest.raises(TimeoutError):
            w.WatchStore._backup(source, target)
        assert time.perf_counter() - start < 2
    finally:
        blocker.close()
        target.close()
        source.close()


def test_backup_timeout_withdraws_publication(tmp_path, monkeypatch):
    path = tmp_path/'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    def timeout(*args):
        raise TimeoutError()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store, '_backup', timeout)
            with pytest.raises(TimeoutError):
                store.heartbeat()
        assert store.lost and not path.exists()
        assert store.db.execute('SELECT live FROM lease').fetchone() == (0,)
        assert not list(Path(store.writer_path).parent.glob('.watch-*'))
        with pytest.raises(ValueError):
            store.heartbeat()
    finally:
        store.close()
