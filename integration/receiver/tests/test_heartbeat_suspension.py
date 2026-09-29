from unittest.mock import patch
import pytest
from dimaggi_receiver import topology_watch as w
from test_lease_fencing import live
from test_watch_current import read
from test_topology_watch import T


def test_heartbeat_keeps_owner_check_wall_sample(tmp_path):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start), patch.object(w.time, 'monotonic', return_value=1000):
        store, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'time', side_effect=[start+10, start+100]), patch.object(w.time, 'monotonic', return_value=1010):
            store.heartbeat()
        assert store.db.execute('SELECT heartbeat FROM lease').fetchone()[0] == start+10
        with patch.object(w.time, 'time', return_value=start+100):
            with pytest.raises(ValueError): read(tmp_path/'w.db')
    finally: store.close()


@pytest.mark.parametrize('elapsed', [45, 100])
def test_heartbeat_rechecks_monotonic_before_commit(tmp_path, elapsed):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start), patch.object(w.time, 'monotonic', return_value=1000):
        store, _ = live(tmp_path/'w.db')
    try:
        ticks = iter([1010, 1000+elapsed])
        with patch.object(w.time, 'time', side_effect=[start+10, start+100]), patch.object(w.time, 'monotonic', side_effect=lambda: next(ticks, 1000+elapsed)):
            with pytest.raises(ValueError, match='expired'): store.heartbeat()
        assert store.db.execute('SELECT live FROM lease').fetchone()[0] == 0
        with patch.object(w.time, 'time', return_value=start+1), patch.object(w.time, 'monotonic', return_value=1001):
            with pytest.raises(ValueError): store.heartbeat()
    finally: store.close()
