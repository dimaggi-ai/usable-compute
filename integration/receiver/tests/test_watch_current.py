import json
import sqlite3
import pytest
from dimaggi_receiver import topology_watch as watch
from test_topology_watch import listing, T, E


def read(path, now=T):
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
