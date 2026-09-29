from copy import deepcopy
import pytest
from dimaggi_receiver.infrastructure import cpu_binding, plan
from dimaggi_receiver.jsonio import digest
from dimaggi_receiver.topology_watch import WatchStore
from test_infrastructure import fixture, NOW


def topology():
    store = WatchStore(':memory:', 'synthetic-tenant', 'synthetic-cluster', 'nodes')
    store.relist({'apiVersion':'v1', 'kind':'NodeList', 'metadata':{'resourceVersion':'12'},
                  'items':[{'metadata':{'name':'host', 'uid':'host-uid', 'resourceVersion':'12'}}]},
                 '2026-09-20T12:00:00Z', '2026-09-20T12:03:00Z')
    result = store.snapshot(NOW); store.close()
    return result


def test_binding_v2_identity_and_inventory_expiry():
    r,q = fixture.fixtures(); p = plan(r,q,digest(r),NOW); t = topology()
    b = cpu_binding(r,q,digest(r),p,NOW,topology=t,node_uid='host-uid')
    assert b['schema'] == 'dimaggi-infrastructure-cpu-binding/v2'
    assert len(b) == 22
    assert b['valid_until'] == b['inventory_expires_at'] == t['expires_at']
    assert b['topology_snapshot_digest'] == 'sha256:' + t['snapshot_id']
    assert b['topology_session'] == t['session']
    assert b['node_uid'] == 'host-uid' and b['topology_resource_version'] == '12'


@pytest.mark.parametrize('change', ['resync', 'expired', 'uid', 'digest', 'scope'])
def test_binding_refuses_unusable_inventory(change):
    from dimaggi_receiver.observations import _digest
    r,q = fixture.fixtures(); p = plan(r,q,digest(r),NOW); t = topology()
    if change == 'resync': t['resync_required'] = True
    if change == 'expired': t['expires_at'] = NOW
    if change == 'uid': t['records']['host']['metadata']['uid'] = 'other'
    if change == 'scope': t['scope'][1] = 'other'
    if change != 'digest': t['snapshot_id'] = _digest({k:v for k,v in t.items() if k != 'snapshot_id'})
    else: t['snapshot_id'] = '0'*64
    with pytest.raises(ValueError): cpu_binding(r,q,digest(r),p,NOW,topology=t,node_uid='host-uid')


def test_fractional_collector_timestamps_are_supported():
    from dimaggi_receiver.observations import _digest
    r,q=fixture.fixtures();p=plan(r,q,digest(r),NOW);t=topology()
    t['observed_at']='2026-09-20T12:00:00.500000Z'
    t['expires_at']='2026-09-20T12:03:00.500000Z'
    t['snapshot_id']=_digest({k:v for k,v in t.items() if k!='snapshot_id'})
    b=cpu_binding(r,q,digest(r),p,NOW,topology=t,node_uid='host-uid')
    assert b['valid_until']=='2026-09-20T12:03:00Z'
    assert b['inventory_expires_at']==t['expires_at']


def test_fractional_expiry_cannot_emit_empty_whole_second_binding():
    from dimaggi_receiver.observations import _digest
    r,q=fixture.fixtures();p=plan(r,q,digest(r),NOW);t=topology()
    t['expires_at']='2026-09-20T12:00:01.500000Z'
    t['snapshot_id']=_digest({k:v for k,v in t.items() if k!='snapshot_id'})
    with pytest.raises(ValueError,match='validity exhausted'):
        cpu_binding(r,q,digest(r),p,NOW,topology=t,node_uid='host-uid')
