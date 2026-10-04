from copy import deepcopy
from decimal import Decimal, localcontext
import pytest
from dimaggi_receiver import outcome_metrics as metrics
from test_outcome_metrics import sample


def test_A1_overallocated_meter_refused_across_tenants_and_window_labels():
    a = sample(); b = deepcopy(a); b.update(tenant='other', window_id='retry')
    for row in b['attempts']: row['id'] += '-other'
    a['energy']['allocation_fraction'] = b['energy']['allocation_fraction'] = '0.8'
    with pytest.raises(ValueError, match='meter allocation exceeds one'):
        metrics.reconcile_allocations([a,b])


def test_A2_cost_follows_fraction_and_remainder_is_unattributed():
    a = sample(); a['energy']['allocation_fraction'] = '0.25'
    assert Decimal(metrics.outcome_metrics(a)['cost_usd']) == Decimal('0.5')
    r = metrics.reconcile_allocations([a])
    assert Decimal(r['unattributed_fraction']) == Decimal('.75')
    assert Decimal(r['unattributed_energy_j']) == 750
    assert Decimal(r['unattributed_cost_usd']) == Decimal('1.5')


def test_exact_sum_and_consistent_meter_required():
    a=sample(); b=deepcopy(a); b['tenant']='b'
    for row in b['attempts']: row['id'] += '-other'
    a['energy']['allocation_fraction']='0.9999999999999999999999999999'
    b['energy']['allocation_fraction']='0.0000000000000000000000000002'
    with localcontext() as ctx:
        ctx.prec=6
        with pytest.raises(ValueError, match='meter allocation exceeds one'): metrics.reconcile_allocations([a,b])
    a['energy']['allocation_fraction']=b['energy']['allocation_fraction']='0.1'
    b['energy']['samples'][0]['watts']=101
    with pytest.raises(ValueError): metrics.reconcile_allocations([a,b])


@pytest.mark.parametrize('tenant', ['tenant-a', 'alias-tenant'])
def test_attempt_alias_refused_regardless_of_tenant_or_window(tenant):
    a = sample(); b = deepcopy(a)
    b.update(tenant=tenant, window_id='alias')
    a['energy']['allocation_fraction'] = b['energy']['allocation_fraction'] = '.5'
    with pytest.raises(ValueError, match='duplicate attempt'):
        metrics.reconcile_allocations([a, b])


def test_overlapping_interval_alias_refused():
    a = sample(); b = deepcopy(a)
    b.update(tenant='alias', window_id='alias', start_s=1)
    b['energy']['samples'][0]['start_s'] = 1
    b['cost']['intervals'][0]['start_s'] = 1
    a['energy']['allocation_fraction'] = b['energy']['allocation_fraction'] = '.5'
    with pytest.raises(ValueError): metrics.reconcile_allocations([a, b])


def test_distinct_attempts_share_exact_meter_interval():
    a = sample(); b = deepcopy(a); b.update(tenant='other', window_id='other')
    for row in b['attempts']: row['id'] += '-other'
    a['energy']['allocation_fraction'] = b['energy']['allocation_fraction'] = '.5'
    assert metrics.reconcile_allocations([a, b])['allocated_fraction'] == '1.0'
