from test_watch_current import ledger
from copy import deepcopy
import pytest
from dimaggi_receiver.infrastructure import cpu_binding, plan, stamp
from dimaggi_receiver.jsonio import digest
from dimaggi_receiver.topology_watch import WatchStore, read_current
from dimaggi_receiver.observations import _utc
from test_infrastructure import fixture, NOW


@pytest.fixture
def live(tmp_path):
    path = tmp_path/'watch.db'
    store = WatchStore(path, 'synthetic-tenant', 'synthetic-cluster', 'nodes')
    store.relist({'apiVersion':'v1', 'kind':'NodeList', 'metadata':{'resourceVersion':'12'},
                  'items':[{'metadata':{'name':'host', 'uid':'host-uid', 'resourceVersion':'12'}}]},
                 '2026-09-20T12:00:00Z', '2026-09-20T12:03:00Z')
    def read():
        return read_current(path, expiry_ledger=ledger(path), tenant='synthetic-tenant', cluster='synthetic-cluster', collection='nodes', now=NOW)
    yield store, read
    store.close()


def bind(t, tenant='synthetic-tenant'):
    r,q = fixture.fixtures(); p = plan(r,q,digest(r),NOW)
    return cpu_binding(r,q,digest(r),p,NOW,topology=t,node_uid='host-uid',tenant=tenant)


def test_binding_v2_identity_and_inventory_expiry(live):
    store, read = live; t = read(); b = bind(t)
    assert b['schema'] == 'dimaggi-infrastructure-cpu-binding/v2'
    assert len(b) == 23
    assert b['valid_until'] == b['inventory_expires_at'] == t['expires_at']
    assert b['topology_snapshot_digest'] == 'sha256:' + t['snapshot_id']
    assert b['topology_session'] == t['session']
    assert b['node_uid'] == 'host-uid' and b['node_name'] == 'host'
    assert b['topology_resource_version'] == '12'


@pytest.mark.parametrize('change', ['resync', 'expired', 'uid', 'digest', 'scope'])
def test_binding_refuses_modified_receipt(live, change):
    store, read = live; t = read()
    if change == 'resync': t['resync_required'] = True
    if change == 'expired': t['expires_at'] = NOW
    if change == 'uid': t['records']['host']['metadata']['uid'] = 'other'
    if change == 'scope': t['scope'][1] = 'other'
    if change == 'digest': t['snapshot_id'] = '0'*64
    with pytest.raises(ValueError): bind(t)


def test_self_certified_topology_is_refused(live):
    store, read = live
    with pytest.raises(ValueError): bind(dict(read()))


def test_tenant_mismatch_and_closed_or_changed_store(live):
    store, read = live; t = read()
    with pytest.raises(ValueError): bind(t, tenant='other')
    store._transaction(lambda value: dict(value, resource_version='13'))
    with pytest.raises(ValueError): bind(t)
    t = read(); store.close()
    with pytest.raises(ValueError): bind(t)


def test_fractional_collector_timestamps_are_supported(live):
    store, read = live
    store._transaction(lambda t: dict(t, observed_at='2026-09-20T12:00:00.500000Z', expires_at='2026-09-20T12:03:00.500000Z'))
    b=bind(read())
    assert b['valid_until']=='2026-09-20T12:03:00Z'
    assert _utc(b['inventory_expires_at'])==_utc('2026-09-20T12:03:00.5Z')


def test_fractional_expiry_cannot_emit_empty_whole_second_binding(live):
    store, read = live
    store._transaction(lambda t: dict(t, expires_at='2026-09-20T12:00:01.500000Z'))
    with pytest.raises(ValueError,match='validity exhausted'): bind(read())


def test_raw_expiry_is_clamped(live):
    store, read = live
    store._transaction(lambda t: dict(t, expires_at='2099-01-01T00:00:00Z'))
    assert bind(read())['inventory_expires_at']=='2026-09-20T12:05:00Z'


@pytest.mark.parametrize('name', ['Bad_Node', 'a'*254, 'other'])
def test_node_name_must_pair_and_be_dns_name(live, name):
    store, read = live
    def mutate(t):
        t['records']['host']['metadata']['name'] = name
        return t
    store._transaction(mutate)
    with pytest.raises(ValueError): bind(read())


@pytest.mark.parametrize('text', ['2026-09-20T12:05:00Z','2026-09-20T12:05:00.5Z','2026-09-20T12:05:00.123456Z'])
def test_strict_timestamp_positive(text):
    assert stamp(text) == _utc(text)


@pytest.mark.parametrize('text', ['2026-09-20T12:05:00,5Z','2026-09-20T12:05:00.1234567Z',
    '2026-09-20T12:05:00+00:00','2026-09-20t12:05:00Z','2026-09-20 12:05:00Z',
    '2026-02-30T12:05:00Z','2026-09-20T24:00:00Z','2026-09-20T12:05:60Z',
    '２０２６-09-20T12:05:00Z'] )
def test_strict_timestamp_negative(text):
    for parser in (stamp, _utc):
        with pytest.raises(ValueError): parser(text)
