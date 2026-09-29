from unittest.mock import patch
import pytest
from dimaggi_receiver import topology_watch as w
from test_lease_fencing import live
from test_watch_current import read
from test_topology_watch import T, E


@pytest.mark.parametrize('observer', ['reader', 'heartbeat', 'write'])
def test_expiry_is_durable_across_rollback(tmp_path, observer):
    path = tmp_path/'watch.db'
    with patch.object(w.time, 'time', return_value=1000):
        s, payload = live(path)
    try:
        with patch.object(w.time, 'time', return_value=1046):
            with pytest.raises(ValueError):
                if observer == 'reader': read(path)
                elif observer == 'heartbeat': s.heartbeat()
                else: s._relist(payload, T, E)
        with patch.object(w.time, 'time', return_value=1001):
            with pytest.raises(ValueError): read(path)
            with pytest.raises(ValueError): s.heartbeat()
            replacement = w.WatchStore(path, 't', 'c', 'nodes')
            with pytest.raises(ValueError): read(path)
            replacement.relist(payload, T, E)
            assert read(path)['issues'] == []
            replacement.close()
    finally: s.close()


def test_suspended_collector_cannot_heartbeat_after_wall_rollback(tmp_path):
    with patch.object(w.time, 'monotonic', return_value=100):
        s, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'monotonic', return_value=146):
            with pytest.raises(ValueError): s.heartbeat()
    finally: s.close()
