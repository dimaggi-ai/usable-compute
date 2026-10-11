import sys
from dimaggi_receiver.observations import ObservationStore
from dimaggi_receiver.topology_watch import WatchStore


def test_darwin_sqlite_connections_request_full_sync(monkeypatch):
    monkeypatch.setattr(sys,'platform','darwin')
    with ObservationStore(':memory:') as store:
        assert store.db.execute('PRAGMA fullfsync').fetchone()[0]==1
        assert store.db.execute('PRAGMA checkpoint_fullfsync').fetchone()[0]==1
    store=WatchStore(':memory:','t','c','nodes')
    try:
        assert store.db.execute('PRAGMA fullfsync').fetchone()[0]==1
        assert store.db.execute('PRAGMA checkpoint_fullfsync').fetchone()[0]==1
    finally: store.close()
