import math
from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_clock_tolerance import iso


@pytest.mark.parametrize('lead', [3, 10, 60, 200])
def test_collector_lead_read_window_ends_at_s_plus_45(state, lead):
    store, path, ledger, heartbeat = state
    death = heartbeat-lead
    before = ledger.read_bytes()
    # The collector's final heartbeat is S seconds ahead of reader time at death.
    with patch.object(w.time, 'time', return_value=death):
        with pytest.raises(ValueError, match='heartbeat is in the future'):
            read(path, ledger, iso(death))
    assert ledger.read_bytes() == before
    for wall in [death+lead+1, math.nextafter(death+lead+45, -math.inf)]:
        with patch.object(w.time, 'time', return_value=wall):
            assert not read(path, ledger, iso(wall))['issues']
    with patch.object(w.time, 'time', return_value=death+lead+45):
        with pytest.raises(ValueError, match='closed or expired'):
            read(path, ledger, iso(death+lead+45))
