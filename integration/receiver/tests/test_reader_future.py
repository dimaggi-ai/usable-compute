import math
from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso


@pytest.mark.parametrize('offset', [3, 10])
def test_future_heartbeat_does_not_tombstone(state, offset):
    store, path, ledger, start = state
    before = ledger.read_bytes()
    with patch.object(w.time, 'time', return_value=start-offset):
        with pytest.raises(ValueError, match='collector heartbeat is in the future'):
            read(path, ledger, now=iso(start-offset))
    assert ledger.read_bytes() == before
    assert not read(path, ledger)['issues']
    store.heartbeat()
    assert not read(path, ledger)['issues']


@pytest.mark.parametrize('edge', ['future', 'expiry'])
@pytest.mark.parametrize('outside', [False, True])
def test_exact_heartbeat_edges(state, edge, outside):
    store, path, ledger, start = state
    store._transaction(lambda v: dict(v, observed_at=iso(start-10)))
    if edge == 'future':
        wall = math.nextafter(start-2, -math.inf) if outside else start-2
    else:
        wall = start+45 if outside else math.nextafter(start+45, -math.inf)
    before = ledger.read_bytes()
    with patch.object(w.time, 'time', return_value=wall):
        if outside:
            with pytest.raises(ValueError, match='future' if edge == 'future' else 'closed or expired'):
                read(path, ledger, now=iso(wall))
        else:
            assert not read(path, ledger, now=iso(wall))['issues']
    assert ledger.read_bytes() == before
    assert not read(path, ledger)['issues']


def test_closed_future_lease_still_tombstones(state):
    store, path, ledger, start = state
    store.db.execute('UPDATE lease SET live=0, heartbeat=?', (start+10,))
    with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
    store.db.execute('UPDATE lease SET live=1, heartbeat=?', (start,))
    with pytest.raises(ValueError, match='previously expired'): read(path, ledger)
