"""Readers verify kernel publication evidence, including suspended collectors."""
import os
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest
from dimaggi_receiver import topology_watch as w


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


@pytest.fixture
def published(tmp_path):
    path, ledger = tmp_path/'watch.db', tmp_path/'ledger'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    now = time.time()
    store.relist({'apiVersion': 'v1', 'kind': 'NodeList',
                  'metadata': {'resourceVersion': '1'}, 'items': []}, iso(now), iso(now+290))
    w.initialize_expiry_ledger(ledger)
    yield store, path, ledger
    store.close()


def read(path, ledger):
    return w.read_current(path, expiry_ledger=ledger, tenant='t', cluster='c',
                          collection='nodes', now=iso(time.time()))


def test_late_rename_refused_without_death(published):
    store, path, ledger = published
    # Checked H predates the real kernel rename by more than B. This deterministic
    # injection isolates reader evidence from collector cleanup and mocked clocks.
    store.db.execute('UPDATE lease SET heartbeat=?', (time.time()-w.COMMIT_BOUND_SECONDS-1,))
    store._publish()
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='publication'): read(path, ledger)
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('phase', ['commit', 'backup', 'file_fsync', 'replace', 'directory_fsync', 'bound'])
def test_stopped_publication_is_never_late_current(tmp_path, phase, stop_delay=0):
    # Each phase stops once before rename until H is late, then once after rename
    # before collector retirement. The parent reads at both actual SIGSTOPs.
    code = r'''
import os, signal, stat, sys, time, sqlite3
from datetime import datetime, timezone
from dimaggi_receiver import topology_watch as w
w.COMMIT_BOUND_SECONDS = 0.1
iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat().replace('+00:00','Z')
s=w.WatchStore(sys.argv[1], 't','c','nodes'); t=time.time()
s.relist({'apiVersion':'v1','kind':'NodeList','metadata':{'resourceVersion':'1'},'items':[]},iso(t),iso(t+290))
phase=sys.argv[2]; fired=set()
def stop(key):
 if key not in fired:
  fired.add(key); print(key,flush=True)
  if key == "after": time.sleep(float(sys.argv[3]))
  os.kill(os.getpid(),signal.SIGSTOP)
db=s.db
class Proxy:
 def __getattr__(self,k): return getattr(db,k)
 def execute(self,sql,*a):
  r=db.execute(sql,*a)
  if sql=='COMMIT' and phase=='commit': stop('before')
  return r
 def backup(self,*a,**k):
  r=db.backup(*a,**k)
  if phase=='backup': stop('before')
  return r
s.db=Proxy()
fsync=os.fsync; replace=os.replace; publish=s._publish
# For stops after rename, make the checked renewal late before that rename.
def rename(*a):
 if phase in ('replace','directory_fsync','bound'): stop('before')
 r=replace(*a)
 if phase=='replace': stop('after')
 return r
def sync(fd):
 r=fsync(fd)
 directory=stat.S_ISDIR(os.fstat(fd).st_mode)
 if not directory and phase=='file_fsync': stop('before')
 if directory and phase=='directory_fsync': stop('after')
 return r
def pub():
 publish()
 stop('after')
os.replace=rename; os.fsync=sync; s._publish=pub
try:s.heartbeat()
except ValueError: print('retired',s.lost,flush=True)
s.close()
'''
    path, ledger = tmp_path/'db', tmp_path/'ledger'
    w.initialize_expiry_ledger(ledger)
    p = subprocess.Popen([sys.executable, '-c', code, str(path), phase, str(stop_delay)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    import select
    def stage(expected):
        assert select.select([p.stdout], [], [], 5)[0]
        assert p.stdout.readline().strip() == expected
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pid, status = os.waitpid(p.pid, os.WUNTRACED | os.WNOHANG)
            if pid:
                assert os.WIFSTOPPED(status) and os.WSTOPSIG(status) == signal.SIGSTOP
                return
            time.sleep(0.001)
        pytest.fail('child did not stop after stage marker')
    try:
        stage('before')
        assert not read(path, ledger)['issues']
        time.sleep(0.25)
        os.kill(p.pid, signal.SIGCONT)
        stage('after')
        # Reader uses the same small B as the child, with actual kernel ctime.
        old = w.COMMIT_BOUND_SECONDS
        try:
            w.COMMIT_BOUND_SECONDS = 0.1
            before = ledger.read_bytes()
            with pytest.raises(ValueError, match='publication'): read(path, ledger)
            assert ledger.read_bytes() == before
        finally:
            w.COMMIT_BOUND_SECONDS = old
        os.kill(p.pid, signal.SIGCONT)
        out, err = p.communicate(timeout=5)
        assert p.returncode == 0, err
        assert 'retired True' in out
    finally:
        if p.poll() is None:
            p.kill(); p.wait(timeout=3)


def test_projection_write_renews_checked_heartbeat(published):
    store, path, ledger = published
    store.db.execute('UPDATE lease SET heartbeat=?', (time.time()-20,))
    old = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    store._transaction(lambda value: value)
    assert store.db.execute('SELECT heartbeat FROM lease').fetchone()[0] > old+19
    assert not read(path, ledger)['issues']


@pytest.mark.parametrize('delay', [10, 10.0001, 11.9])
def test_ctime_bound_has_no_cross_host_tolerance(published, monkeypatch, delay):
    from types import SimpleNamespace
    store, path, ledger = published
    heartbeat = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    inode = path.stat()
    original = os.fstat
    def stamp(fd):
        info = original(fd)
        if (info.st_dev, info.st_ino) != (inode.st_dev, inode.st_ino): return info
        fields = {k: getattr(info, k) for k in dir(info) if k.startswith('st_')}
        fields['st_ctime'] = heartbeat+delay
        return SimpleNamespace(**fields)
    monkeypatch.setattr(os, 'fstat', stamp)
    before = ledger.read_bytes()
    if delay <= 10: assert not read(path, ledger)['issues']
    else:
        with pytest.raises(ValueError, match='publication'): read(path, ledger)
    assert ledger.read_bytes() == before


def test_path_replacement_cannot_substitute_inode_evidence(published, monkeypatch):
    store, path, ledger = published
    original = os.open
    fired = False
    def swap(p, *args, **kwargs):
        nonlocal fired
        fd = original(p, *args, **kwargs)
        if str(p) == str(path) and not fired:
            fired = True
            store.heartbeat()
        return fd
    monkeypatch.setattr(os, 'open', swap)
    before = ledger.read_bytes()
    with pytest.raises(ValueError): read(path, ledger)
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('migrate', [False, True])
def test_first_open_publication_has_checked_kernel_stamp(tmp_path, migrate):
    import shutil
    path = tmp_path/'db'
    if migrate:
        old = w.WatchStore(path, 't', 'c', 'nodes')
        old.close()
        shutil.rmtree(path.with_name(path.name+'.collector'))
    before = time.time()
    store = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        _, _, _, lease, _, stamp = w._read_rows(path)
        assert before <= lease[1] <= stamp <= time.time()
        assert stamp-lease[1] <= w.COMMIT_BOUND_SECONDS
    finally:
        store.close()


@pytest.mark.parametrize('phase', ['file_fsync', 'replace'])
def test_publication_stage_marker_precedes_kernel_stop(tmp_path, phase):
    test_stopped_publication_is_never_late_current(tmp_path, phase, stop_delay=0.1)
