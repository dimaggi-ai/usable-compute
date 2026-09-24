"""Actual process death, alias contention, and reopen on the running OS."""
import os
from pathlib import Path
import subprocess
import sys
import pytest
from dimaggi_receiver.observations import ObservationStore, ObservationError


def env():
    return {**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}


def test_killed_writer_keeps_committed_state_releases_lock(tmp_path):
    path=tmp_path/'journal.db'
    code='''
import sys,time
from dimaggi_receiver.observations import ObservationStore
s=ObservationStore(sys.argv[1])
s.register_source('collector','workload','target')
print('committed',flush=True)
time.sleep(60)
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(path)],env=env(),stdout=subprocess.PIPE,text=True)
    try:
        import selectors
        ready=selectors.DefaultSelector();ready.register(process.stdout,selectors.EVENT_READ)
        assert ready.select(10), 'writer failed to become ready'
        assert process.stdout.readline().strip()=='committed'
        with pytest.raises(ObservationError,match='active application writer'):ObservationStore(path)
        alias=tmp_path/'alias.db';os.link(path,alias)
        with pytest.raises(ObservationError,match='active application writer'):ObservationStore(alias)
        process.kill();process.wait(timeout=10)
        with ObservationStore(path) as store:
            assert store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert store.db.execute('SELECT count(*) FROM sources').fetchone()[0]==1
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stdout.close()
