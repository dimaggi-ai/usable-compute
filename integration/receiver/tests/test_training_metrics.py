import pytest
from dimaggi_receiver.training_metrics import training_metrics
from test_outcome_metrics import sample

def progress():
    pin='sha256:'+'a'*64
    return {'schema':'dimaggi-training-progress/v1','criterion_digest':pin,'direction':'at_least','target':0.9,
        'observations':[{'time_s':0,'quality':0.5,'evaluator_digest':pin},{'time_s':10,'quality':0.95,'evaluator_digest':pin}]}

def test_training_known_answer_and_misaligned_target():
    value=training_metrics(sample(),progress())
    assert value['energy_to_target_joules']=='1000' and value['cost_to_target_usd']=='2.0' and value['time_to_target_s']=='10'
    p=progress();p['observations'][1]['time_s']=5
    assert training_metrics(sample(),p)['energy_to_target_joules'] is None
    p=progress();p['target']=0.99
    assert training_metrics(sample(),p)['time_to_target_s'] is None

def test_training_evaluator_change_and_time_reversal():
    p=progress();p['observations'][1]['evaluator_digest']='changed'
    with pytest.raises(ValueError):training_metrics(sample(),p)
    p=progress();p['observations'].reverse()
    with pytest.raises(ValueError):training_metrics(sample(),p)
