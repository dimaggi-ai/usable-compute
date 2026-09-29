from test_watch_current import ledger
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from dimaggi_receiver import topology_watch as w
from test_lease_fencing import live
from test_topology_watch import T


@pytest.mark.parametrize('offset', [61, 301])
def test_older_caller_now_cannot_refresh_projection(tmp_path, offset):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start):
        s, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'time', return_value=start+offset):
            # A healthy lease does not make an expired projection current.
            s.db.execute('UPDATE lease SET heartbeat=?', (start+offset,))
            with pytest.raises(ValueError):
                w.read_current(tmp_path/'w.db', expiry_ledger=ledger(tmp_path/'w.db'), tenant='t', cluster='c', collection='nodes', now=T)
    finally: s.close()


def test_tolerated_now_does_not_control_freshness(tmp_path):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start): s, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'time', return_value=start+60):
            s.db.execute('UPDATE lease SET heartbeat=?', (start+60,))
            old = datetime.fromtimestamp(start+59, timezone.utc).isoformat().replace('+00:00','Z')
            with pytest.raises(ValueError):
                w.read_current(tmp_path/'w.db', expiry_ledger=ledger(tmp_path/'w.db'), tenant='t', cluster='c', collection='nodes', now=old)
    finally: s.close()
