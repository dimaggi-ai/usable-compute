from copy import deepcopy
import pytest
from dimaggi_receiver.topology import TopologyState
from dimaggi_receiver import topology_watch
from test_topology import event, dra
from test_topology_watch import store, listing, T, E


def test_dra_cross_source_conflict():
    state=TopologyState('t','c'); e=event(); e.update(adapter='kubernetes-dra',producer_version='1.34.0',payload=dra())
    state.apply(e); other=deepcopy(e); other['source']='other'
    other['payload']['items'][0]['metadata']['uid']='different'
    state.apply(other)
    assert 'graph:source_conflict' in state.snapshot(e['observed_at'])['issues']


def test_failed_relist_taints_current_inventory(store):
    bad=listing(); bad['metadata']['continue']='next'
    with pytest.raises(ValueError): store.relist(bad,T,E)
    assert 'resync_required' in store.snapshot(T)['issues']


def test_empty_watch_cannot_refresh_observation(store):
    store.apply([], '2026-09-24T00:00:30Z', '2026-09-24T00:05:30Z')
    assert store.snapshot(T)['observed_at']==T
    assert store.snapshot(T)['expires_at']==E


def test_bookmark_envelope_validated(store):
    with pytest.raises(ValueError):
        store.apply([{'type':'BOOKMARK','object':{'apiVersion':'apps/v1','kind':'Deployment','metadata':{'resourceVersion':'20'}}}],T,E)
    assert 'resync_required' in store.snapshot(T)['issues']


def test_conflicting_scoped_inventory_refused(store):
    first=store.snapshot(T); other=deepcopy(first); other['records']['n1']['metadata']['uid']='other'
    with pytest.raises(ValueError): topology_watch.validate_inventory_agreement([first, other])


def test_typed_bookmark_and_heartbeat_invalidate_snapshot(store):
    from test_topology_watch import event as watch_event
    before=store.snapshot(T)
    store.apply([{'type':'BOOKMARK','object':{'apiVersion':'v1','kind':'Node','metadata':{'resourceVersion':'20'}}}],T,E)
    bookmarked=store.snapshot(T)
    assert bookmarked['snapshot_id'] != before['snapshot_id']
    store.apply([watch_event(rv='21')],T,E)
    assert store.snapshot(T)['snapshot_id'] != bookmarked['snapshot_id']
    assert store.snapshot(T)['records']['n1']['metadata']['uid']=='uid1'
