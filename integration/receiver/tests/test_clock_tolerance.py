from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from dimaggi_receiver import topology_watch as w
from test_lease_fencing import live
from test_watch_current import read
from test_topology_watch import T


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


@pytest.mark.parametrize('step', [0.5, 2])
def test_small_backward_step_does_not_kill_generation(tmp_path, step):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start):
        store, _ = live(tmp_path/'w.db')
        store._transaction(lambda value: dict(value, observed_at=iso(start-10)))
    try:
        with patch.object(w.time, 'time', return_value=start-step):
            assert not read(tmp_path/'w.db')['issues']
            store.heartbeat()
        with patch.object(w.time, 'time', return_value=start+5):
            assert not read(tmp_path/'w.db')['issues']
            store.heartbeat()
    finally: store.close()


@pytest.mark.parametrize('observer', ['reader', 'collector'])
@pytest.mark.parametrize('step', [2.001, 10])
def test_larger_backward_step_never_revives(tmp_path, observer, step):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start): store, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'time', return_value=start-step):
            with pytest.raises(ValueError):
                if observer == 'reader': read(tmp_path/'w.db')
                else: store.heartbeat()
        with patch.object(w.time, 'time', return_value=start+5):
            with pytest.raises(ValueError): read(tmp_path/'w.db')
            if observer == 'collector':
                with pytest.raises(ValueError): store.heartbeat()
    finally: store.close()


def test_small_step_with_future_projection_does_not_record_dead_lease(tmp_path):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start): store, _ = live(tmp_path/'w.db')
    try:
        with patch.object(w.time, 'time', return_value=start-0.5):
            with pytest.raises(ValueError, match='stale_or_future'): read(tmp_path/'w.db')
        with patch.object(w.time, 'time', return_value=start+5):
            assert not read(tmp_path/'w.db')['issues']
            store.heartbeat()
    finally: store.close()


def test_single_two_second_step_adds_at_most_two_seconds(tmp_path):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start): store, _ = live(tmp_path/'w.db')
    try:
        # Clock stepped back two seconds: real elapsed 46, wall elapsed 44.
        with patch.object(w.time, 'time', return_value=start+46-2):
            assert not read(tmp_path/'w.db')['issues']
        # At real elapsed 47, the unchanged 45-second wall-age boundary refuses.
        with patch.object(w.time, 'time', return_value=start+47-2):
            with pytest.raises(ValueError): read(tmp_path/'w.db')
        with patch.object(w.time, 'time', return_value=start+44):
            with pytest.raises(ValueError): read(tmp_path/'w.db')
    finally: store.close()
