import json
import sqlite3
import pytest
from dimaggi_receiver import topology_watch as watch
from test_topology_watch import listing, T, E


def read(path, now=None):
    from datetime import datetime, timezone
    if now is None: now = datetime.fromtimestamp(watch.time.time(), timezone.utc).isoformat().replace("+00:00", "Z")
    return watch.read_current(path, tenant='t', cluster='c', collection='nodes', namespace='', now=now)


def test_read_current_is_read_only_consistent_and_fails_closed(tmp_path):
    path = tmp_path/'watch.db'
    with pytest.raises(ValueError):
        read(path)
    assert not path.exists()
    store = watch.WatchStore(path, 't', 'c', 'nodes')
    payload = listing(); payload['metadata']['resourceVersion'] = '12'
    store.relist(payload, T, E)
    try:
        assert read(path) == store.snapshot(T)
        assert store.session == read(path)['session']
        with pytest.raises(ValueError): read(path, E)
        store.fail()
        with pytest.raises(ValueError): read(path)
        store.relist(payload, T, E)
        row = json.loads(store.db.execute('SELECT body FROM projection').fetchone()[0])
        row['records']['n1']['metadata']['uid'] = 'replacement'
        store.db.execute('UPDATE projection SET body=?', (json.dumps(row),))
        with pytest.raises(ValueError): read(path)
    finally:
        store.close()


def test_current_reader_closes_its_connection(tmp_path, monkeypatch):
    path=tmp_path/'watch.db'
    store=watch.WatchStore(path,'t','c','nodes')
    payload=listing();payload['metadata']['resourceVersion']='12'
    store.relist(payload,T,E)
    original=sqlite3.connect; opened=[]
    def connect(*args, **kwargs):
        connection=original(*args, **kwargs);opened.append(connection);return connection
    monkeypatch.setattr(sqlite3,'connect',connect)
    read(path)
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        opened[0].execute('SELECT 1')
    store.close()


def test_closed_collector_is_not_current(tmp_path):
    path = tmp_path/'watch.db'
    store = watch.WatchStore(path, 't', 'c', 'nodes')
    payload = listing(); payload['metadata']['resourceVersion'] = '12'
    store.relist(payload, T, E)
    store.close()
    with pytest.raises(ValueError):
        read(path)


def test_crashed_collector_lease_expires(tmp_path, monkeypatch):
    import time
    path = tmp_path/'watch.db'
    store = watch.WatchStore(path, 't', 'c', 'nodes')
    payload = listing(); payload['metadata']['resourceVersion'] = '12'
    store.relist(payload, T, E)
    try:
        assert read(path)['issues'] == []
        future = time.time() + 46
        monkeypatch.setattr(time, 'time', lambda: future)
        with pytest.raises(ValueError):
            read(path)
    finally:
        store.close()


def test_heartbeat_refreshes_lease_and_future_clock_refuses(tmp_path, monkeypatch):
    import time
    path = tmp_path/'watch.db'
    store = watch.WatchStore(path, 't', 'c', 'nodes')
    payload = listing(); payload['metadata']['resourceVersion'] = '12'
    store.relist(payload, T, E)
    initial = time.time()
    try:
        monkeypatch.setattr(time, 'time', lambda: initial + 40)
        store.heartbeat()
        monkeypatch.setattr(time, 'time', lambda: initial + 50)
        assert read(path)['issues'] == []
        monkeypatch.setattr(time, 'time', lambda: initial)
        with pytest.raises(ValueError): read(path)
    finally: store.close()
