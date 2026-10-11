import os
import sqlite3
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from dimaggi_receiver.observations import ObservationError


@pytest.mark.parametrize('successor', [False, True])
@pytest.mark.parametrize('entry', ['heartbeat', 'close'])
def test_lost_close_releases_resources_without_restoring_lock(tmp_path, successor, entry):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    lock = Path(store.writer_path + '.lease-lock')
    replacement = lock.with_name('replacement')
    replacement.touch(mode=0o600)
    os.replace(replacement, lock)
    with pytest.raises(ObservationError, match='placement unavailable'):
        getattr(store, entry)()
    assert store.lost and not path.exists()
    next_store = w.WatchStore(path, 't', 'c', 'nodes') if successor else None
    before = (path.read_bytes(), path.stat().st_ino) if successor else None
    store.close()
    store.close()
    assert store.closed and store._lease_pin.closed and store._published_fd is None
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        store.db.execute('SELECT 1')
    if successor:
        assert (path.read_bytes(), path.stat().st_ino) == before
        next_store.heartbeat()
        next_store.close()
    else:
        assert not path.exists()


def test_retired_close_preserves_terminal_publication_without_placement(tmp_path):
    path = tmp_path / 'db'
    store = w.WatchStore(path, 't', 'c', 'nodes')
    store.last_tick -= w.LEASE_SECONDS + 1
    with pytest.raises(ObservationError, match='closed or expired'):
        store.heartbeat()
    assert store._retired_published and store.lost
    before = path.read_bytes(), path.stat().st_ino
    Path(store.writer_path + '.lease-lock').unlink()
    store.close()
    assert store.closed and store._lease_pin.closed
    assert (path.read_bytes(), path.stat().st_ino) == before
