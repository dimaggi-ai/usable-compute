import concurrent.futures
import fcntl
import threading
import time

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso
from dimaggi_receiver import expiry_ledger


@pytest.mark.parametrize('stall', ['lock', 'fsync'])
def test_ledger_wait_does_not_block_collector(state, monkeypatch, stall):
    store, path, ledger, start = state
    entered = threading.Event()
    release = threading.Event()
    original = w.check_generation
    def check(*args):
        if stall == 'lock': entered.set()
        return original(*args)
    monkeypatch.setattr(w, 'check_generation', check)
    if stall == 'fsync':
        main_thread = threading.get_ident()
        monkeypatch.setattr(w.time, 'time', lambda: start if threading.get_ident() == main_thread else start+57)
        fsync = expiry_ledger.os.fsync
        def stalled_sync(fd):
            if threading.get_ident() != main_thread:
                entered.set()
                assert release.wait(3)
            return fsync(fd)
        monkeypatch.setattr(expiry_ledger.os, 'fsync', stalled_sync)
    # A short writer timeout makes any retained reader transaction observable.
    store.db.execute('PRAGMA busy_timeout=200')
    with ledger.open('r+') as holder, concurrent.futures.ThreadPoolExecutor(1) as pool:
        if stall == 'lock': fcntl.flock(holder, fcntl.LOCK_EX)
        job = pool.submit(read, path, ledger, iso(start+57) if stall == 'fsync' else iso(start))
        try:
            assert entered.wait(2)
            began = time.perf_counter()
            store.heartbeat()
            assert time.perf_counter() - began < 0.5
        finally:
            release.set()
            fcntl.flock(holder, fcntl.LOCK_UN)
        if stall == 'fsync':
            with pytest.raises(ValueError, match='closed or expired'): job.result(timeout=3)
        else:
            assert not job.result(timeout=3)['issues']


@pytest.mark.parametrize('change', ['owner', 'heartbeat', 'live', 'generation', 'digest', 'identity'])
def test_changed_database_during_ledger_step_refuses(state, monkeypatch, change):
    store, path, ledger, start = state
    original = w.check_generation
    store.db.execute('PRAGMA busy_timeout=200')
    def check(*args):
        original(*args)
        if change == 'digest':
            store.db.execute("UPDATE projection SET digest='changed'")
            store._publish()
        elif change == 'identity':
            store.db.execute("UPDATE store_identity SET identity='changed'")
            store._publish()
        else:
            replacement = {'owner': 'other', 'heartbeat': start+1, 'live': 0, 'generation': 'other'}[change]
            store.db.execute(f'UPDATE lease SET {change}=?', (replacement,))
            store._publish()
    monkeypatch.setattr(w, 'check_generation', check)
    if change == 'heartbeat':
        assert not read(path, ledger)['issues']
    else:
        reason = 'closed or expired' if change == 'live' else 'watch changed during reader verification'
        with pytest.raises(ValueError, match=reason): read(path, ledger)


@pytest.mark.parametrize('age', [45.1, 47.1])
def test_renewal_during_ledger_wait_does_not_tombstone(state, monkeypatch, age):
    store, path, ledger, start = state
    original = w.check_generation
    def check(*args):
        original(*args)
        monkeypatch.setattr(w.time, 'time', lambda: start+44.5)
        store.heartbeat()
        monkeypatch.setattr(w.time, 'time', lambda: start+age)
    monkeypatch.setattr(w, 'check_generation', check)
    with pytest.raises(ValueError): read(path, ledger)
    assert ledger.read_text().count('\n') == 1
    monkeypatch.setattr(w, 'check_generation', original)
    assert not read(path, ledger, iso(start+age))['issues']
