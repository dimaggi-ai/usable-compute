import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read


def test_every_read_can_straddle_a_successful_renewal(published, monkeypatch):
    store, path, ledger = published
    original = w._read_rows
    calls = 0
    def renew(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls % 2 == 0: store.heartbeat()
        return original(*args, **kwargs)
    monkeypatch.setattr(w, '_read_rows', renew)
    for _ in range(20): assert not read(path, ledger)['issues']
    assert ledger.read_text().count('\n') == 1


@pytest.mark.parametrize('change', ['closed', 'late', 'identity', 'projection'])
def test_second_snapshot_rederives_refusal(published, monkeypatch, change):
    store, path, ledger = published
    original = w._read_rows
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == 'closed': store.close()
            elif change == 'late':
                store.db.execute('UPDATE lease SET heartbeat=heartbeat-11')
                store._publish()
            elif change == 'identity':
                store.db.execute("UPDATE store_identity SET identity='other'")
                store._publish()
            else: store.fail()
        return original(*args, **kwargs)
    monkeypatch.setattr(w, '_read_rows', changed)
    with pytest.raises(ValueError): read(path, ledger)


def test_second_ledger_wait_cannot_extend_serving_cutoff(published, monkeypatch):
    store, path, ledger = published
    heartbeat = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    original = w.check_generation
    calls = 0
    def wait(*args):
        nonlocal calls
        calls += 1
        result = original(*args)
        if calls == 2: monkeypatch.setattr(w.time, 'time', lambda: heartbeat+46)
        return result
    monkeypatch.setattr(w, 'check_generation', wait)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
    assert ledger.read_bytes() == before
