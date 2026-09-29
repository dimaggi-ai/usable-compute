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


def test_consumption_requires_persistent_replay_store(tmp_path, monkeypatch):
    m,p,t=fixture(); args=(enc(m),enc(p),enc(t)); receipt=enc(admission.admit_supplied_batch(*args))
    with pytest.raises(ValueError): admission.consume_supplied_batch(*args,receipt)
    path=tmp_path/'replay.db'
    admission.provision_replay_store(path, tmp_path/'anchor.json')
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(tmp_path/'anchor.json'))
    assert admission.consume_supplied_batch(*args,receipt)==p['rows']
    with pytest.raises(ValueError, match='replay'):
        admission.consume_supplied_batch(*args,receipt)


def test_records_invokes_admission_and_binds_scope(tmp_path, monkeypatch):
    record=record_fixture(); m,p,t=fixture()
    supplied={item['evidence']:{'manifest':enc(m),'payload':enc(p),'trust':enc(t)} for item in record['dispositions']}
    admission.provision_replay_store(tmp_path/'replay.db', tmp_path/'anchor.json')
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(tmp_path/'anchor.json'))
    with pytest.raises(ValueError,match='scope'):
        records.assess(record,NOW,artifacts=supplied)


def test_records_admission_success_and_restart_replay_refusal(tmp_path, monkeypatch):
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
    admission.provision_replay_store(path, tmp_path/'anchor.json')
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(tmp_path/'anchor.json'))
    result=records.assess(record,NOW,artifacts=supplied)
    assert result['recorded_gate']=='recorded_criteria_met'
    assert result['useful_dispositions']==2
    assert not result['continuation_authorized']
    with pytest.raises(ValueError,match='replay'): records.assess(record,NOW,artifacts=supplied)


@pytest.mark.parametrize('override', [False, True])
def test_caller_created_replay_store_cannot_credit(tmp_path, override):
    m, p, t = fixture(); args = (enc(m), enc(p), enc(t))
    receipt = enc(admission.admit_supplied_batch(*args))
    class Noop(admission.ReplayStore):
        def claim_many(self, batches): pass
    cls = Noop if override else admission.ReplayStore
    with cls(tmp_path/'fresh.db', create=True) as store:
        with pytest.raises(ValueError):
            admission.consume_supplied_batch(*args, receipt, replay_store=store)


@pytest.mark.parametrize('attack', ['rollback', 'fresh', 'anchor_rollback'])
def test_designated_store_identity_and_head(tmp_path, monkeypatch, attack):
    m,p,t=fixture(); args=(enc(m),enc(p),enc(t)); receipt=enc(admission.admit_supplied_batch(*args))
    path=tmp_path/'replay.db'; anchor=tmp_path/'anchor.json'
    admission.provision_replay_store(path, anchor)
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(anchor))
    old_db=path.read_bytes(); old_anchor=anchor.read_bytes()
    admission.consume_supplied_batch(*args,receipt)
    if attack == 'rollback': path.write_bytes(old_db)
    elif attack == 'anchor_rollback': anchor.write_bytes(old_anchor)
    else:
        admission.provision_replay_store(tmp_path/'fresh.db', tmp_path/'fresh.json')
        path.write_bytes((tmp_path/'fresh.db').read_bytes())
    with pytest.raises(ValueError, match='anchor_mismatch'):
        admission.consume_supplied_batch(*args,receipt)
