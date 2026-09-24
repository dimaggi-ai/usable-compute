"""Aligned-window outcome metrics with explicit energy and cost boundaries.

Inputs are attributed evidence, not verified meter truth. Power samples use the
stated piecewise-constant interval model; cumulative counters do not prove peak
power. Failed work and allocated idle costs stay in the whole-window numerator.
"""
from decimal import Decimal
from .observations import _digest, _json, _identifier, ObservationError
from .topology import need

CONTEXT = {'model','tokenizer','precision','workload','quality_policy','latency_slo',
           'cache_policy','batching','input_length','output_length','allocation_rule'}


def number(value, name, *, positive=False):
    need(type(value) in (int,float,str), name + ': numeric value required')
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ObservationError(name + ': invalid decimal') from exc
    need(result.is_finite() and (result > 0 if positive else result >= 0), name + ': invalid bound')
    return result


def count(value, name):
    need(type(value) is int and value >= 0, name + ': nonnegative integer required')
    return value


def outcome_metrics(data):
    need(type(data) is dict and set(data) == {'schema','window_id','tenant','start_s','end_s','context','attempts',
         'energy','cost','power_budget_w'}, 'invalid metric fields')
    need(data['schema'] == 'dimaggi-outcome-input/v1', 'unknown outcome schema')
    need(len(_json(data).encode()) <= 8*1024*1024, 'metric input too large')
    for key in ('window_id','tenant'):
        _identifier(data[key],key)
    need(type(data['context']) is dict and set(data['context']) == CONTEXT, 'incomplete comparison context')
    for key,value in data['context'].items():
        _identifier(value,key)
    start,end=number(data['start_s'],'start'),number(data['end_s'],'end')
    need(end>start,'empty measurement window'); duration=end-start
    budget=number(data['power_budget_w'],'power budget',positive=True)
    attempts=data['attempts']; need(type(attempts) is list and len(attempts)<=100000,'invalid attempt list')
    ids=set(); accepted=0; successful_tasks=0
    tokens={'input':0,'output':0,'cache':0,'speculative':0,'discarded':0}
    statuses={key:0 for key in ('succeeded','failed','rejected','deferred')}
    for row in attempts:
        need(type(row) is dict and set(row)=={'id','status','quality_pass','latency_pass','successful_task','tokens'},'invalid attempt')
        name=_identifier(row['id'],'attempt ID');need(name not in ids,'duplicate attempt');ids.add(name)
        need(row['status'] in statuses,'unknown outcome')
        need(all(type(row[k]) is bool for k in ('quality_pass','latency_pass','successful_task')),'boolean outcome flags required')
        statuses[row['status']]+=1
        need(type(row['tokens']) is dict and set(row['tokens'])==set(tokens),'incomplete token accounting')
        for key,value in row['tokens'].items():tokens[key]+=count(value,key)
        need(row['tokens']['discarded']<=row['tokens']['output'],'discarded exceeds output')
        qualified=row['status']=='succeeded' and row['quality_pass'] and row['latency_pass']
        need(not row['successful_task'] or qualified,'unqualified successful task')
        if qualified:
            accepted+=row['tokens']['output']-row['tokens']['discarded']
            successful_tasks+=int(row['successful_task'])
    issues=[]; joules=None; peak=None; energy_kind='unknown'; energy_scope=None
    e=data['energy']
    if e is None:
        issues.append('energy_missing')
    else:
        need(type(e) is dict and set(e)=={'kind','scope','meter_id','allocation_fraction','uncertainty_fraction','includes_failed_idle',
             'method','max_gap_s','samples'},'invalid energy contract')
        need(e['kind'] in ('measured','modelled'),'energy evidence class required')
        need(e['scope'] in ('device','host','facility'),'unsupported measurement boundary')
        _identifier(e['meter_id'],'meter ID')
        fraction=number(e['allocation_fraction'],'allocation fraction');need(fraction<=1,'allocation exceeds whole meter')
        uncertainty=number(e['uncertainty_fraction'],'uncertainty');need(uncertainty<=1,'invalid uncertainty')
        need(e['includes_failed_idle'] is True,'failed/retry/idle exclusion unsupported in primary metric')
        need(e['method'] in ('power_intervals','energy_counter'),'unknown energy integration method')
        max_gap=number(e['max_gap_s'],'max sample gap',positive=True)
        samples=e['samples'];need(type(samples) is list and len(samples)<=100000,'invalid sample list')
        energy_kind=e['kind'];energy_scope=e['scope']
        values=[];energy=Decimal(0);cursor=start; valid=True
        if e['method']=='power_intervals':
            for row in samples:
                need(type(row) is dict and set(row)=={'start_s','end_s','watts'},'invalid power interval')
                a,b,w=number(row['start_s'],'sample start'),number(row['end_s'],'sample end'),number(row['watts'],'watts')
                need(start<=a<b<=end,'power interval outside window')
                need(a>=cursor,'duplicate/overlapping/unordered power intervals')
                if a!=cursor or b-a>max_gap:valid=False
                energy+=(b-a)*w;values.append(w);cursor=b
            if cursor!=end or not samples:valid=False
            if valid:peak=max(values);joules=energy*fraction
        else:
            previous=None
            for row in samples:
                need(type(row) is dict and set(row)=={'time_s','joules','epoch'},'invalid counter sample')
                t,j=number(row['time_s'],'counter time'),number(row['joules'],'counter joules')
                epoch=_identifier(row['epoch'],'counter epoch');need(start<=t<=end,'counter outside window')
                if previous:
                    pt,pj,pe=previous;need(t>pt,'duplicate/unordered counter samples')
                    if epoch!=pe or j<pj or t-pt>max_gap:valid=False
                    else:energy+=j-pj
                elif t!=start:valid=False
                previous=(t,j,epoch)
            if len(samples)<2 or previous[0]!=end:valid=False
            if valid:joules=energy*fraction
            issues.append('peak_power_unmeasured')
        if not valid:issues.append('energy_incomplete_or_counter_reset')
    c=data['cost'];cost=None;cost_kind='unknown';cost_scope=None
    if c is None:
        issues.append('cost_missing')
    else:
        need(type(c) is dict and set(c)=={'kind','basis','currency','includes_failed_idle','intervals'},'invalid cost contract')
        need(c['kind'] in ('accounted','modelled') and c['basis'] in ('electricity_only','full_allocated') and c['currency']=='USD','unsupported cost basis')
        need(c['includes_failed_idle'] is True,'failed/retry/idle cost exclusion unsupported')
        need(type(c['intervals']) is list and len(c['intervals'])<=100000,'invalid cost intervals')
        cost_kind,cost_scope=c['kind'],c['basis'];cursor=start;total=Decimal(0);complete=True
        for row in c['intervals']:
            need(type(row) is dict and set(row)=={'start_s','end_s','usd_per_second'},'invalid cost rate interval')
            a,b,rate=number(row['start_s'],'cost start'),number(row['end_s'],'cost end'),number(row['usd_per_second'],'cost rate')
            need(start<=a<b<=end and a>=cursor,'overlapping/unordered/outside cost interval')
            if a!=cursor:complete=False
            total+=(b-a)*rate;cursor=b
        if cursor!=end:complete=False
        if complete:cost=total
        else:issues.append('cost_incomplete')
    if not accepted:issues.append('accepted_token_denominator_zero')
    if joules==0:issues.append('energy_denominator_zero')
    if not successful_tasks:issues.append('successful_task_denominator_zero')
    def divide(n,d,scale=1):
        return str(Decimal(n)*scale/Decimal(d)) if n is not None and d is not None and d>0 else None
    # Budget compares total meter peak to the stated budget, not allocated average.
    budget_state='unknown' if peak is None else ('within' if peak<=budget else 'exceeded')
    result={'schema':'dimaggi-outcome-metrics/v1','input_digest':_digest(data),'window_id':data['window_id'],'tenant':data['tenant'],
            'context_digest':_digest(data['context']),'start_s':str(start),'end_s':str(end),'duration_s':str(duration),'accepted_output_tokens':accepted,
            'token_counts':tokens,'outcome_counts':statuses,'successful_tasks':successful_tasks,
            'energy_attribution':{k:v for k,v in e.items() if k!='samples'} if e else None,
            'energy_j':str(joules) if joules is not None else None,'energy_kind':energy_kind,'energy_scope':energy_scope,
            'cost_usd':str(cost) if cost is not None else None,'cost_kind':cost_kind,'cost_basis':cost_scope,
            'tokens_per_joule':divide(accepted,joules),'joules_per_accepted_token':divide(joules,accepted),
            'kwh_per_million_accepted_tokens':divide(joules,accepted,Decimal(1000000)/3600000),
            'usd_per_million_accepted_tokens':divide(cost,accepted,1000000),
            'joules_per_successful_task':divide(joules,successful_tasks),'usd_per_successful_task':divide(cost,successful_tasks),
            'accepted_tokens_per_second':divide(accepted,duration),'average_attributed_watts':divide(joules,duration),
            'meter_peak_watts':str(peak) if peak is not None else None,'power_budget_watts':str(budget),
            'power_budget_state':budget_state,'throughput_under_power_budget':divide(accepted,duration) if budget_state=='within' else None,
            'issues':issues,'execution_authorized':False}
    return result
