from copy import deepcopy
from decimal import Decimal, localcontext
import pytest
from dimaggi_receiver import outcome_metrics as metrics
from test_outcome_metrics import sample


def test_A1_overallocated_meter_refused_across_tenants_and_window_labels():
    a = sample(); b = deepcopy(a); b.update(tenant='other', window_id='retry')
    a['energy']['allocation_fraction'] = b['energy']['allocation_fraction'] = '0.8'
    with pytest.raises(ValueError, match='allocation'):
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
    a['energy']['allocation_fraction']='0.9999999999999999999999999999'
    b['energy']['allocation_fraction']='0.0000000000000000000000000002'
    with localcontext() as ctx:
        ctx.prec=6
        with pytest.raises(ValueError): metrics.reconcile_allocations([a,b])
    a['energy']['allocation_fraction']=b['energy']['allocation_fraction']='0.1'
    b['energy']['samples'][0]['watts']=101
    with pytest.raises(ValueError): metrics.reconcile_allocations([a,b])
