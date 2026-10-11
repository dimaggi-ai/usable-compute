from decimal import Decimal, localcontext
import pytest
from dimaggi_receiver.outcome_metrics import outcome_metrics
from dimaggi_receiver.training_metrics import training_metrics
from test_outcome_metrics import sample


def test_averages_and_models_cannot_prove_power_peak():
    d=sample()
    assert outcome_metrics(d)['power_budget_state']=='unknown'
    d['energy']['kind']='modelled'
    assert outcome_metrics(d)['throughput_under_power_budget'] is None


def test_decimal_context_cannot_change_metric_and_large_values_refuse():
    d=sample();d['energy']['samples'][0]['watts']='106.17283917'
    with localcontext() as ctx:
        ctx.prec=6; low=outcome_metrics(d)
        ctx.prec=28; high=outcome_metrics(d)
    assert low==high
    d['energy']['samples'][0]['watts']='9e999999'
    with pytest.raises(ValueError): outcome_metrics(d)
    d['energy']['samples'][0]['watts']='1000000000000000000000000000009'
    assert Decimal(outcome_metrics(d)['energy_j'])==Decimal('10000000000000000000000000000090')


def progress():
    pin='sha256:'+'a'*64
    return {'schema':'dimaggi-training-progress/v1','criterion_digest':pin,'direction':'at_most','target':-2,
            'observations':[{'time_s':10,'quality':-3,'evaluator_digest':pin}]}


def test_training_signed_quality_and_invalid_window():
    assert training_metrics(sample(),progress())['target_reached']
    with pytest.raises(ValueError): training_metrics(None,progress())


def test_measured_one_second_series_has_explicit_budget_basis():
    d=sample();d['energy']['samples']=[{'start_s':i,'end_s':i+1,'watts':100} for i in range(10)]
    r=outcome_metrics(d)
    assert r['power_budget_state']=='within'
    assert r['power_budget_basis']=='measured_interval_average_at_most_1s'
