import json
from unittest.mock import patch
import pytest
from dimaggi_receiver import topology_watch as w
from test_watch_current import read
from test_topology_watch import listing, T, E


def live(path):
    s = w.WatchStore(path, 't', 'c', 'nodes')
    payload = listing(); payload['metadata']['resourceVersion'] = '12'
    s.relist(payload, T, E)
    return s, payload


@pytest.mark.parametrize('failure', ['scope', 'crash'])
def test_failed_acquisition_does_not_publish_lease(tmp_path, failure):
    path = tmp_path/'watch.db'; old, payload = live(path)
    old.close()
    with pytest.raises((ValueError, RuntimeError)):
        if failure == 'scope': w.WatchStore(path, 't', 'wrong', 'nodes')
        else:
            with patch.object(w.WatchStore, '_transaction', side_effect=RuntimeError('injected crash')):
                w.WatchStore(path, 't', 'c', 'nodes')
    with pytest.raises(ValueError): read(path)


@pytest.mark.parametrize('operation', ['relist', 'apply', 'bookmark'])
def test_displaced_writer_cannot_commit(tmp_path, operation):
    path = tmp_path/'watch.db'; old, payload = live(path)
    new, _ = live(path)
    before = new.db.execute('SELECT body,digest FROM projection').fetchone()
    try:
        with patch.object(old, 'heartbeat', side_effect=RuntimeError('after commit')):
            with pytest.raises((ValueError, RuntimeError)):
                if operation == 'relist': old._relist(payload, T, E)
                else:
                    old.session = new.session
                    events = [] if operation == 'apply' else [{'type':'BOOKMARK','object':{'apiVersion':'v1','kind':'Node','metadata':{'resourceVersion':'13'}}}]
                    old.apply(events, T, E)
        assert new.db.execute('SELECT body,digest FROM projection').fetchone() == before
    finally: old.close(); new.close()


def test_generation_requires_its_own_relist(tmp_path):
    path = tmp_path/'watch.db'; old, payload = live(path)
    new = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        # Even intact prior contents cannot be relabelled current by clearing resync.
        row = json.loads(new.db.execute('SELECT body FROM projection').fetchone()[0])
        row['resync_required'] = False
        new.db.execute('UPDATE projection SET body=?,digest=?', (json.dumps(row), w._digest(row)))
        with pytest.raises(ValueError): read(path)
        new.relist(payload, T, E)
        assert read(path)['issues'] == []
    finally: old.close(); new.close()


def test_acquisition_rollback_preserves_prior_owner_and_projection(tmp_path):
    path = tmp_path/'watch.db'; old, _ = live(path)
    lease = old.db.execute('SELECT * FROM lease').fetchone()
    projection = old.db.execute('SELECT * FROM projection').fetchone()
    # Abort the projection invalidation after acquisition has attempted its write.
    old.db.execute("CREATE TRIGGER reject_projection BEFORE INSERT ON projection BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
        w.WatchStore(path, 't', 'c', 'nodes')
    assert old.db.execute('SELECT * FROM lease').fetchone() == lease
    assert old.db.execute('SELECT * FROM projection').fetchone() == projection
    assert not read(path)['issues']
    old.close()
