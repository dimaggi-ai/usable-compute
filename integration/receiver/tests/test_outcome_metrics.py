from copy import deepcopy
from decimal import Decimal
import pytest
from dimaggi_receiver.outcome_metrics import outcome_metrics, CONTEXT


def sample():
    return {'schema':'dimaggi-outcome-input/v1','window_id':'w','tenant':'t','start_s':0,'end_s':10,
            'context':{k:'fixed' for k in CONTEXT}, 'power_budget_w':150,
            'attempts':[{'id':'a','status':'succeeded','quality_pass':True,'latency_pass':True,'successful_task':True,
                         'tokens':{'input':50,'output':110,'cache':0,'speculative':20,'discarded':10}},
                        {'id':'b','status':'failed','quality_pass':False,'latency_pass':False,'successful_task':False,
                         'tokens':{'input':20,'output':40,'cache':0,'speculative':0,'discarded':40}}],
            'energy':{'kind':'measured','scope':'host','meter_id':'m','allocation_fraction':1,'uncertainty_fraction':0.1,
                      'includes_failed_idle':True,'method':'power_intervals','max_gap_s':10,
                      'samples':[{'start_s':0,'end_s':10,'watts':100}]},
            'cost':{'kind':'modelled','basis':'full_allocated','currency':'USD','includes_failed_idle':True,
                    'intervals':[{'start_s':0,'end_s':5,'usd_per_second':0.1},{'start_s':5,'end_s':10,'usd_per_second':0.3}]}}


def test_independent_known_answer_includes_failed_cost():
    r=outcome_metrics(sample())
    assert r['energy_j']=='1000' and r['accepted_output_tokens']==100
    assert Decimal(r['tokens_per_joule'])==Decimal('0.1')
    assert Decimal(r['joules_per_accepted_token'])==10
    assert Decimal(r['usd_per_million_accepted_tokens'])==20000
    assert Decimal(r['joules_per_successful_task'])==1000
    assert Decimal(r['throughput_under_power_budget'])==10
    assert r['outcome_counts']['failed']==1


@pytest.mark.parametrize('field',['energy','cost'])
def test_missing_stays_unknown(field):
    d=sample();d[field]=None;r=outcome_metrics(d)
    assert r['energy_j' if field=='energy' else 'cost_usd'] is None


def test_gaps_resets_zero_and_budget():
    d=sample();d['energy']['samples'][0]['end_s']=9
    assert outcome_metrics(d)['energy_j'] is None
    d=sample();d['energy'].update(method='energy_counter',samples=[{'time_s':0,'joules':100,'epoch':'a'},{'time_s':10,'joules':50,'epoch':'a'}])
    assert outcome_metrics(d)['energy_j'] is None
    d['energy']['samples'][1]['joules']=1100;r=outcome_metrics(d)
    assert r['energy_j']=='1000' and r['throughput_under_power_budget'] is None
    d=sample();d['attempts']=[];r=outcome_metrics(d)
    assert r['joules_per_accepted_token'] is None and r['usd_per_successful_task'] is None
    d=sample();d['power_budget_w']=50
    assert outcome_metrics(d)['power_budget_state']=='exceeded'


def test_duplicate_overlap_nan_and_scope_refused():
    edits=[lambda d:d['attempts'].append(deepcopy(d['attempts'][0])),
           lambda d:d['energy']['samples'].append(deepcopy(d['energy']['samples'][0])),
           lambda d:d['energy']['samples'][0].update(watts='NaN'),
           lambda d:d['energy'].update(scope='TDP'),
           lambda d:d['energy'].update(includes_failed_idle=False),
           lambda d:d['cost']['intervals'][1].update(start_s=4),
           lambda d:d['context'].pop('tokenizer')]
    for edit in edits:
        d=sample();edit(d)
        with pytest.raises(ValueError):outcome_metrics(d)


def test_shared_meter_and_modelled_energy_are_explicit():
    d=sample();d['energy'].update(allocation_fraction='0.5',kind='modelled');r=outcome_metrics(d)
    assert Decimal(r['energy_j'])==500 and r['energy_kind']=='modelled'
    assert r['meter_peak_watts']=='100'
