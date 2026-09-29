import math
import sqlite3
from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso


@pytest.mark.parametrize('age', [45, 47, 56.999999, 57])
def test_expiry_band_and_permanent_edge(state, monkeypatch, age):
    store, path, ledger, start = state
    before = ledger.read_bytes()
    monkeypatch.setattr(w.time, 'time', lambda: start+age)
    with pytest.raises(ValueError, match='closed or expired'):
        read(path, ledger, iso(start+age))
    assert (ledger.read_bytes() != before) == (age >= 57)
    monkeypatch.setattr(w.time, 'time', lambda: start)
    if age >= 57:
        with pytest.raises(ValueError, match='previously expired'): read(path, ledger)
    else:
        assert not read(path, ledger)['issues']


@pytest.mark.parametrize('journal', ['delete', 'wal'])
def test_renewal_before_first_wall_sample(state, monkeypatch, journal):
    store, path, ledger, start = state
    assert store.db.execute('PRAGMA journal_mode='+journal).fetchone()[0] == journal
    original = w.os.open
    fired = False
    # Renew before the descriptor pins the reader's first snapshot.
    def open_snapshot(*args, **kwargs):
        nonlocal fired
        if not fired and str(args[0]) == str(path):
            fired = True
            with patch.object(w.time, 'time', return_value=start+44.5): store.heartbeat()
        return original(*args, **kwargs)
    monkeypatch.setattr(w.os, 'open', open_snapshot)
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
def test_pending_checked_renewal_refuses_without_permanent_death(tmp_path, monkeypatch, journal):
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
        with pytest.raises(ValueError): reader.result(timeout=3)
        assert not read(path, ledger, iso(wall[0]))['issues']
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


def test_reader_ignores_obsolete_sampling_lock(state):
    import fcntl
    store, path, ledger, start = state
    before = ledger.read_bytes()
    sidecar = path.with_name(path.name+'.lease-lock')
    sidecar.touch()
    with sidecar.open('rb') as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        assert not read(path, ledger)['issues']
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('edge', [45, 57])
@pytest.mark.parametrize('direction', [-1, 0, 1])
def test_exact_representable_band_edges(state, monkeypatch, edge, direction):
    store, path, ledger, start = state
    wall = start + edge
    if direction:
        wall = math.nextafter(wall, math.inf if direction > 0 else -math.inf)
    monkeypatch.setattr(w.time, 'time', lambda: wall)
    before = ledger.read_bytes()
    if wall - start < 45:
        assert not read(path, ledger, iso(wall))['issues']
    else:
        with pytest.raises(ValueError): read(path, ledger, iso(wall))
    assert (ledger.read_bytes() != before) == (wall - start >= 57)


def test_slow_snapshot_uses_preopen_time_for_death(state, monkeypatch):
    store, path, ledger, start = state
    wall = [start + 44]
    monkeypatch.setattr(w.time, 'time', lambda: wall[0])
    original = sqlite3.connect
    class SlowLeaseRead:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if sql.startswith('SELECT owner,heartbeat'):
                wall[0] = start + 58
            return result
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        return SlowLeaseRead(db) if kwargs.get('uri') else db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='closed or expired'):
        read(path, ledger, iso(start + 58))
    assert ledger.read_bytes() == before
