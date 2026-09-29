from datetime import timedelta
import json
import pytest
from dimaggi_receiver.topology import TopologyState
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.observations import _utc
from test_topology import event
from test_topology_watch import listing


@pytest.mark.parametrize('watch', [False, True])
def test_fifty_day_snapshot_cannot_claim_2099_expiry(tmp_path, watch):
    observed = '2026-08-01T00:00:00Z'
    expiry = '2099-01-01T00:00:00Z'
    now = '2026-09-20T00:00:00Z'
    if watch:
        store = WatchStore(tmp_path/'watch.db', 't', 'c', 'nodes')
        try:
            store.relist(listing(), observed, expiry)
            assert 'stale_or_future' in store.snapshot(now)['issues']
            store.apply([], observed, expiry)
            assert _utc(store.snapshot(observed)['expires_at']) <= _utc(observed) + timedelta(seconds=300)
            value = store.snapshot(observed)
            value['expires_at'] = expiry
            store.db.execute('UPDATE projection SET body=?', (json.dumps(value),))
            assert 'stale_or_future' in store.snapshot(now)['issues']
        finally:
            store.close()
    else:
        store = TopologyState('t', 'c')
        e = event(); e.update(observed_at=observed, expires_at=expiry)
        store.apply(e)
        assert 'graph:stale_or_future' in store.snapshot(now)['issues']
        assert _utc(store.snapshot(observed)['sources']['graph']['expires_at']) <= _utc(observed) + timedelta(seconds=300)
        store.sources['graph']['event']['expires_at'] = expiry
        assert 'graph:stale_or_future' in store.snapshot(now)['issues']
