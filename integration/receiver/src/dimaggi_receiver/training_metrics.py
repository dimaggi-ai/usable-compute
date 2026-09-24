"""Training progress under a frozen externally evaluated quality target.

No serving-token proxy. Meter and cost boundaries reuse outcome accounting;
all work through the first target observation must be included in that window.
"""
from .outcome_metrics import outcome_metrics, number
from .topology import need
from .observations import _digest


def training_metrics(window, progress):
    need(type(progress) is dict and set(progress)=={'schema','criterion_digest','direction','target','observations'},'training progress contract required')
    need(progress['schema']=='dimaggi-training-progress/v1','unsupported training contract')
    pin=progress['criterion_digest']
    need(type(pin) is str and pin.startswith('sha256:') and len(pin)==71,'frozen quality criterion digest required')
    need(progress['direction'] in ('at_least','at_most'),'unknown quality direction')
    target=number(progress['target'],'target')
    observations=progress['observations']
    need(type(observations) is list and 1<=len(observations)<=10000,'bounded progress observations required')
    start=number(window['start_s'],'start');end=number(window['end_s'],'end')
    previous=None;reached=None
    for row in observations:
        need(type(row) is dict and set(row)=={'time_s','quality','evaluator_digest'},'quality observation required')
        t=number(row['time_s'],'quality time');q=number(row['quality'],'quality')
        need(start<=t<=end and (previous is None or t>previous),'quality observations must be ordered inside measurement window')
        need(row['evaluator_digest']==pin,'quality evaluator changed')
        if reached is None and (q>=target if progress['direction']=='at_least' else q<=target):reached=t
        previous=t
    metrics=outcome_metrics(window)
    issues=[x for x in metrics['issues'] if x not in ('accepted_token_denominator_zero','successful_task_denominator_zero','energy_denominator_zero')]
    complete=reached is not None and reached==end
    return {'schema':'dimaggi-training-metrics/v1','window_digest':_digest(window),'progress_digest':_digest(progress),
        'criterion_digest':pin,'target_reached':reached is not None,
        'time_to_target_s':str(reached-start) if reached is not None else None,
        'energy_to_target_joules':metrics['energy_j'] if complete else None,
        'cost_to_target_usd':metrics['cost_usd'] if complete else None,
        'issues':issues+([] if complete else ['target_not_reached_or_window_not_aligned']),
        'execution_authorized':False,'claim':'time to first observed target; quality supplied by evaluator; no independent acceptance implied'}
