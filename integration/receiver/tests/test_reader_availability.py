"""Collector progress must not depend on reader progress."""
import concurrent.futures
import fcntl
import threading
import time

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read


@pytest.mark.parametrize('operation', ['heartbeat', 'projection'])
def test_stalled_digest_does_not_delay_collector(state, monkeypatch, operation):
    store, path, ledger, start = state
    entered, release = threading.Event(), threading.Event()
    original = w._digest
    main = threading.get_ident()
    def digest(value):
        if threading.get_ident() != main:
            entered.set()
            assert release.wait(8)
        return original(value)
    monkeypatch.setattr(w, '_digest', digest)
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        reader = pool.submit(read, path, ledger)
        assert entered.wait(2)
        try:
            began = time.perf_counter()
            if operation == 'heartbeat':
                store.heartbeat()
            else:
                store._transaction(lambda prior: prior)
            assert time.perf_counter() - began < 0.5
        finally:
            release.set()
        assert not reader.result(timeout=3)['issues']


def test_obsolete_sidecar_holder_cannot_block_heartbeat(state):
    store, path, ledger, start = state
    sidecar = path.with_name(path.name + '.lease-lock')
    sidecar.touch(exist_ok=True)
    with sidecar.open('rb') as holder:
        fcntl.flock(holder, fcntl.LOCK_SH)
        began = time.perf_counter()
        store.heartbeat()
        assert time.perf_counter() - began < 0.5
    assert not read(path, ledger)['issues']


@pytest.mark.parametrize('operation', ['heartbeat', 'projection'])
@pytest.mark.parametrize('holder', ['stopped_sql_reader', 'hostile_posix_reader'])
def test_reader_inode_locks_cannot_delay_collector(state, operation, holder):
    import os
    import select
    import signal
    import subprocess
    import sys
    store, path, ledger, start = state
    process = subprocess.Popen([sys.executable, '-c', '''
import fcntl, os, sqlite3, sys
if sys.argv[2] == 'hostile_posix_reader':
    fd = os.open(sys.argv[1], os.O_RDONLY)
    fcntl.lockf(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
else:
    db = sqlite3.connect('file:'+sys.argv[1]+'?mode=ro', uri=True)
    db.execute('BEGIN')
    db.execute('SELECT * FROM lease').fetchall()
print('held', flush=True)
sys.stdin.readline()
''', str(path), holder], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 3)[0]
        assert process.stdout.readline().strip() == 'held'
        os.kill(process.pid, signal.SIGSTOP)
        began = time.perf_counter()
        if operation == 'heartbeat':
            store.heartbeat()
        else:
            store._transaction(lambda prior: prior)
        elapsed = time.perf_counter() - began
        print(f'{holder} {operation}: {elapsed:.6f}s')
        assert elapsed < 0.5
        assert not read(path, ledger)['issues']
    finally:
        if process.poll() is None:
            os.kill(process.pid, signal.SIGCONT)
            process.communicate('\n', timeout=3)
        assert process.returncode == 0


@pytest.mark.parametrize('umask', [0o077, 0o777])
def test_readonly_snapshot_under_restrictive_umask(tmp_path, monkeypatch, umask):
    import os
    import sqlite3
    from test_lease_fencing import live
    from test_topology_watch import T
    mask = os.umask(umask)
    monkeypatch.setattr(w.time, 'time', lambda: w._utc(T).timestamp())
    try:
        store, _ = live(tmp_path/'watch.db')
    finally:
        os.umask(mask)
    ledger = tmp_path/'ledger'
    w.initialize_expiry_ledger(ledger)
    path = tmp_path/'watch.db'
    try:
        assert path.stat().st_mode & 0o777 == 0o400
        assert not path.with_name(path.name+'.lease-lock').exists()
        assert not path.with_name(path.name+'-wal').exists()
        assert not path.with_name(path.name+'-shm').exists()
        path.chmod(0o400)
        with sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True) as db:
            assert db.execute('SELECT live FROM lease').fetchone() == (1,)
        assert not read(path, ledger)['issues']
        store.heartbeat()
        assert path.stat().st_mode & 0o777 == 0o400
    finally:
        store.close()
