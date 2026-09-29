import json
import pytest
import rsi_admission as admission
import rsi_records as records
from test_rsi_admission import fixture, enc
from test_rsi_records import fixture as record_fixture, NOW


def test_records_v1_cannot_credit_unadmitted_references():
    result = records.assess(record_fixture(), NOW)
    assert result['recorded_gate'] == 'insufficient_evidence'
    assert result['useful_dispositions'] == 0


def test_consumption_requires_persistent_replay_store(tmp_path):
    m,p,t=fixture(); args=(enc(m),enc(p),enc(t)); receipt=enc(admission.admit_supplied_batch(*args))
    with pytest.raises(ValueError): admission.consume_supplied_batch(*args,receipt)
    path=tmp_path/'replay.db'
    with admission.ReplayStore(path, create=True) as store:
        assert admission.consume_supplied_batch(*args,receipt,replay_store=store)==p['rows']
    with admission.ReplayStore(path) as store:
        with pytest.raises(ValueError, match='replay'):
            admission.consume_supplied_batch(*args,receipt,replay_store=store)


def test_records_invokes_admission_and_binds_scope(tmp_path):
    record=record_fixture(); m,p,t=fixture()
    supplied={item['evidence']:{'manifest':enc(m),'payload':enc(p),'trust':enc(t)} for item in record['dispositions']}
    with admission.ReplayStore(tmp_path/'replay.db',create=True) as store:
        with pytest.raises(ValueError,match='scope'):
            records.assess(record,NOW,artifacts=supplied,replay_store=store)


def test_records_admission_success_and_restart_replay_refusal(tmp_path):
    from test_rsi_admission import bind, bind_plan
    record=record_fixture(); supplied={}
    for index, item in enumerate(record['dispositions']):
        m,p,t=fixture()
        task=next(task for task in record['tasks'] if task['id']==item['task_id'])
        for binding in (task['manual_binding'], task['assisted_binding']): binding['unit']='count'
        binding=task['assisted_binding']
        for key,target in [('repo','repository'),('snapshot','snapshot'),('profile','profile_id'),
                           ('objective','objective'),('denominator','denominator'),('unit','unit'),('workload','workload')]:
            m['scope'][target]=t['scope'][target]=binding[key]
        p['rows'][0]['source_id'] += str(index)
        p['rows'][0]['row_id'] += str(index)
        part=t['partition_plan']['partitions'][0]
        for key in ('source_id','row_id'): part['members'][0][key]=p['rows'][0][key]
        m['partition']['row_ids']=[p['rows'][0]['row_id']]
        bind_plan(m,t); bind(m,p)
        item['evidence']='synthetic://admitted/'+str(index)
        supplied[item['evidence']]={'manifest':enc(m),'payload':enc(p),'trust':enc(t)}
    path=tmp_path/'records.db'
    with admission.ReplayStore(path,create=True) as store:
        result=records.assess(record,NOW,artifacts=supplied,replay_store=store)
        assert result['recorded_gate']=='recorded_criteria_met'
        assert result['useful_dispositions']==2
        assert not result['continuation_authorized']
    with admission.ReplayStore(path) as store:
        with pytest.raises(ValueError,match='replay'): records.assess(record,NOW,artifacts=supplied,replay_store=store)
