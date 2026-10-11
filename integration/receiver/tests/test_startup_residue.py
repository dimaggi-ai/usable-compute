import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest
from dimaggi_receiver import topology_watch as w


@pytest.mark.parametrize('step', ['allocate1', 'allocate2', 'rename_before', 'rename_after'])
def test_startup_kill_leaves_only_private_residue(tmp_path, step):
    path = tmp_path / 'db'
    child = '''
import os, signal, sys, tempfile
from dimaggi_receiver.topology_watch import WatchStore
step = sys.argv[2]
allocate, replace = tempfile.mkstemp, os.replace
calls = 0
def kill(): os.kill(os.getpid(), signal.SIGKILL)
def mk(*args, **kwargs):
    global calls
    result = allocate(*args, **kwargs)
    calls += 1
    if step == 'allocate' + str(calls): kill()
    return result
def rename(source, target):
    if step == 'rename_before': kill()
    replace(source, target)
    if step == 'rename_after': kill()
tempfile.mkstemp, os.replace = mk, rename
WatchStore(sys.argv[1], 't', 'c', 'nodes')
'''
    result = subprocess.run([sys.executable, '-c', child, str(path), step], timeout=10)
    assert result.returncode == -signal.SIGKILL
    assert {p.name for p in tmp_path.iterdir()} <= {'db', 'db.collector'}
    store = w.WatchStore(path, 't', 'c', 'nodes')
    store.close()
    assert {p.name for p in tmp_path.iterdir()} == {'db', 'db.collector'}
    assert not list(Path(str(path) + '.collector').glob('.watch-*'))
