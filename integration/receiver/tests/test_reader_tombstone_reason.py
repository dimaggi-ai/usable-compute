from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso


@pytest.mark.parametrize('future', ['reader_step', 'heartbeat_rewrite'])
def test_recorded_expiry_precedes_future_diagnostic(state, future):
    store, path, ledger, start = state
    with patch.object(w.time, 'time', return_value=start+47):
        with pytest.raises(ValueError, match='closed or expired'):
            read(path, ledger, iso(start+47))
    before = ledger.read_bytes()
    if future == 'heartbeat_rewrite':
        store.db.execute('UPDATE lease SET heartbeat=?', (start+10,))
    wall = start-5 if future == 'reader_step' else start
    with patch.object(w.time, 'time', return_value=wall):
        with pytest.raises(ValueError, match='collector generation previously expired'):
            read(path, ledger, iso(wall))
    assert ledger.read_bytes() == before
