import math
import sqlite3
from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso


@pytest.mark.parametrize('age', [45, 46.999999, 47])
def test_expiry_band_and_permanent_edge(state, monkeypatch, age):
    store, path, ledger, start = state
    before = ledger.read_bytes()
    monkeypatch.setattr(w.time, 'time', lambda: start+age)
    with pytest.raises(ValueError, match='closed or expired'):
        read(path, ledger, iso(start+age))
    assert (ledger.read_bytes() != before) == (age >= 47)
    monkeypatch.setattr(w.time, 'time', lambda: start)
    if age >= 47:
        with pytest.raises(ValueError, match='previously expired'): read(path, ledger)
    else:
        assert not read(path, ledger)['issues']


@pytest.mark.parametrize('journal', ['delete', 'wal'])
def test_renewal_before_first_wall_sample(state, monkeypatch, journal):
    store, path, ledger, start = state
    assert store.db.execute('PRAGMA journal_mode='+journal).fetchone()[0] == journal
    original = sqlite3.connect
    fired = False
    # Renew just before the reader establishes its first SQL snapshot.
    def connect(*args, **kwargs):
        nonlocal fired
        if not fired:
            fired = True
            with patch.object(w.time, 'time', return_value=start+44.5): store.heartbeat()
        return original(*args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    monkeypatch.setattr(w.time, 'time', lambda: start+45.1)
    assert not read(path, ledger, iso(start+45.1))['issues']
    assert ledger.read_text().count('\n') == 1


@pytest.mark.parametrize('journal', ['delete', 'wal'])
@pytest.mark.parametrize('phase', ['first_commit', 'ledger'])
def test_renewal_after_snapshot_never_records_death(state, monkeypatch, journal, phase):
    store, path, ledger, start = state
    store.db.execute('PRAGMA journal_mode='+journal)
    wall = [start+44]
    monkeypatch.setattr(w.time, 'time', lambda: wall[0])
    original = w.check_generation
    def renew():
        wall[0] = start+44.5
        store.heartbeat()
        wall[0] = start+47.1
    if phase == 'ledger':
        def check(*args):
            result = original(*args)
            renew()
            return result
        monkeypatch.setattr(w, 'check_generation', check)
    else:
        def check(*args):
            renew()
            return original(*args)
        monkeypatch.setattr(w, 'check_generation', check)
    with pytest.raises(ValueError): read(path, ledger, iso(start+44))
    assert ledger.read_text().count('\n') == 1
    monkeypatch.setattr(w, 'check_generation', original)
    assert not read(path, ledger, iso(wall[0]))['issues']


@pytest.mark.parametrize('journal', ['delete', 'wal'])
def test_pending_checked_renewal_finishes_before_expiry_sample(tmp_path, monkeypatch, journal):
    import concurrent.futures
    import threading
    from test_lease_fencing import live
    from test_topology_watch import T
    start = w._utc(T).timestamp()
    wall = [start]
    monkeypatch.setattr(w.time, 'time', lambda: wall[0])
    path, ledger = tmp_path/'watch.db', tmp_path/'ledger'
    w.initialize_expiry_ledger(ledger)
    ready, checked, release = threading.Event(), threading.Event(), threading.Event()
    original = w.WatchStore._check_owner
    armed = [False]
    def pause(store):
        result = original(store)
        if armed[0]:
            checked.set()
            assert release.wait(3)
        return result
    monkeypatch.setattr(w.WatchStore, '_check_owner', pause)
    finish = threading.Event()
    def collector():
        store, _ = live(path)
        store.db.execute('PRAGMA journal_mode='+journal)
        ready.set()
        assert finish.wait(3)
        armed[0] = True
        store.heartbeat()
        # Keep lease live for the read; close only the SQLite connection.
        store.db.close()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        writer = pool.submit(collector)
        assert ready.wait(2)
        wall[0] = start+44.5
        finish.set()
        assert checked.wait(2)
        wall[0] = start+47.1
        reader = pool.submit(read, path, ledger, iso(wall[0]))
        try:
            # Old code can sample/record death while the checked renewal waits.
            threading.Event().wait(0.1)
        finally:
            release.set()
        writer.result(timeout=3)
        result = reader.result(timeout=3)
        assert not result['issues']
    assert ledger.read_text().count('\n') == 1


@pytest.mark.parametrize('age', [45, 46.999999])
def test_collector_two_seconds_behind_can_renew_in_refusal_band(state, monkeypatch, age):
    store, path, ledger, start = state
    monkeypatch.setattr(w.time, 'time', lambda: start+age)
    with pytest.raises(ValueError): read(path, ledger, iso(start+age))
    monkeypatch.setattr(w.time, 'time', lambda: start+age-2)
    store.heartbeat()
    monkeypatch.setattr(w.time, 'time', lambda: start+age)
    assert not read(path, ledger, iso(start+age))['issues']
    assert ledger.read_text().count('\n') == 1


def test_busy_sampling_lock_refuses_without_ledger_write(state, monkeypatch):
    import fcntl
    store, path, ledger, start = state
    before = ledger.read_bytes()
    ticks = iter([0, 6])
    monkeypatch.setattr(w.time, 'perf_counter', lambda: next(ticks))
    with path.open('rb') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match='collector lease sampling busy'):
            read(path, ledger)
    assert ledger.read_bytes() == before
    monkeypatch.undo()
