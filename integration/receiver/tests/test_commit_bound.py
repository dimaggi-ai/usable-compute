"""A commit that outlives its check must retire its generation."""
import os
import signal
import subprocess
import sys
import time

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state


@pytest.mark.parametrize('operation', ['heartbeat', 'projection'])
def test_late_commit_abandons_generation(state, monkeypatch, operation):
    store, path, ledger, start = state
    tick = [store.last_tick]
    monkeypatch.setattr(w.time, 'monotonic', lambda: tick[0])
    bound = getattr(w, 'COMMIT_BOUND_SECONDS', 10)
    db = store.db
    class DelayedCommit:
        def __getattr__(self, name):
            return getattr(db, name)
        def execute(self, sql, *args):
            result = db.execute(sql, *args)
            if sql == 'COMMIT':
                tick[0] += bound + 0.01
            return result
    store.db = DelayedCommit()
    with pytest.raises(ValueError):
        if operation == 'heartbeat':
            store.heartbeat()
        else:
            store._transaction(lambda prior: prior)
    assert db.execute('SELECT live FROM lease').fetchone() == (0,)
    # Wall-clock correction cannot let the same object act again.
    tick[0] = store.last_tick
    with pytest.raises(ValueError):
        store.heartbeat()
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        assert replacement.generation != store.generation
    finally:
        replacement.close()


@pytest.mark.parametrize('operation', ['heartbeat', 'projection'])
def test_sigstop_between_check_and_commit_abandons(tmp_path, operation):
    process = subprocess.Popen([sys.executable, '-c', '''
import os, signal, sys
from dimaggi_receiver import topology_watch as w
w.COMMIT_BOUND_SECONDS = 0.1
store = w.WatchStore(sys.argv[1], 't', 'c', 'nodes')
original = store._check_owner
def checked():
    result = original()
    print('checked', flush=True)
    os.kill(os.getpid(), signal.SIGSTOP)
    return result
store._check_owner = checked
try:
    if sys.argv[2] == 'heartbeat': store.heartbeat()
    else: store._transaction(lambda prior: prior)
except ValueError:
    print('abandoned', store.db.execute('SELECT live FROM lease').fetchone()[0], flush=True)
else:
    print('renewed', flush=True)
store.close()
''', str(tmp_path/'watch.db'), operation], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        import select
        assert select.select([process.stdout], [], [], 3)[0]
        assert process.stdout.readline().strip() == 'checked'
        time.sleep(0.25)
        os.kill(process.pid, signal.SIGCONT)
        output, errors = process.communicate(timeout=3)
        assert process.returncode == 0, errors
        assert output.strip() == 'abandoned 0'
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_forward_wall_step_during_commit_abandons(state, monkeypatch):
    store, path, ledger, start = state
    wall = [start]
    monkeypatch.setattr(w.time, 'time', lambda: wall[0])
    db = store.db
    class ForwardStep:
        def __getattr__(self, name): return getattr(db, name)
        def execute(self, sql, *args):
            result = db.execute(sql, *args)
            if sql == 'COMMIT': wall[0] += w.COMMIT_BOUND_SECONDS + 1
            return result
    store.db = ForwardStep()
    with pytest.raises(ValueError): store.heartbeat()
    assert db.execute('SELECT live FROM lease').fetchone() == (0,)


def test_failed_publication_never_allows_generation_reuse(state, monkeypatch):
    store, path, ledger, start = state
    def fail(): raise OSError('injected publication failure')
    with monkeypatch.context() as change:
        change.setattr(store, '_publish', fail)
        with pytest.raises(OSError): store.heartbeat()
    with pytest.raises(ValueError): store.heartbeat()
    assert store.db.execute('SELECT live FROM lease').fetchone() == (0,)
