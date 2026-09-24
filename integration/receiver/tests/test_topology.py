from copy import deepcopy
import pytest
from dimaggi_receiver.topology import TopologyState, topograph, kubernetes, changed_sources


def event():
    return {'schema':'dimaggi-topology-input/v1','tenant':'t','cluster':'c','source':'graph',
            'epoch':'one','sequence':0,'previous':None,'observed_at':'2026-09-24T00:00:00Z',
            'expires_at':'2026-09-24T01:00:00Z','producer_version':'1.0.0','evidence_class':'replay',
            'adapter':'topograph','mode':'full','payload':{'instances':[{'id':'node-a','network_layers':['leaf-1'], 'labels':{'gpu':'H100'}}]}}


def test_graph_known_answer_and_no_invented_capacity():
    s=TopologyState('t','c'); e=event(); assert s.apply(e)=='changed'; assert s.apply(e)=='duplicate'
    snap=s.snapshot('2026-09-24T00:30:00Z')
    node=snap['sources']['graph']['records']['node-a']
    assert node['attributes']['network_layers']==['leaf-1']
    assert node['capacity']==node['health']=='unknown'
    assert not snap['execution_authorized']
    assert any('stale' in x for x in s.snapshot('2026-09-24T01:00:00Z')['issues'])


def test_deletion_gap_relist_retired_epoch():
    s=TopologyState('t','c'); e=event(); s.apply(e); before=s.snapshot('2026-09-24T00:30:00Z')
    e.update(sequence=1, previous=s.sources['graph']['digest'],payload={'instances':[]}); s.apply(e)
    assert not s.snapshot('2026-09-24T00:30:00Z')['sources']['graph']['records']
    assert changed_sources(before,s.snapshot('2026-09-24T00:30:00Z'))==['graph']
    e['sequence']=3
    with pytest.raises(ValueError): s.apply(e)
    assert any('resync_required' in x for x in s.snapshot('2026-09-24T00:30:00Z')['issues'])
    e.update(epoch='two', sequence=0,previous=None); s.apply(e)
    with pytest.raises(ValueError): s.apply(event())


def test_conflict_and_scope_and_unknown_version():
    s=TopologyState('t','c'); s.apply(event()); bad=event(); bad['payload']['instances'][0]['labels']['gpu']='B200'
    with pytest.raises(ValueError): s.apply(bad)
    assert 'graph' in s.tainted
    for key,value in [('tenant','other'),('producer_version','2.0'),('mode','delta'),('sequence',True)]:
        e=event();e[key]=value
        with pytest.raises(ValueError): TopologyState('t','c').apply(e)


def test_duplicate_instance_and_bound():
    e=event()['payload']; e['instances']*=2
    with pytest.raises(ValueError): topograph(e,version='1.0.0')
    with pytest.raises(ValueError): topograph({'instances':[{}]*10001},version='1.0.0')


def dra():
    return {'apiVersion':'resource.k8s.io/v1','kind':'ResourceSliceList','metadata':{'resourceVersion':'opaque/x'},'items':[
        {'apiVersion':'resource.k8s.io/v1','kind':'ResourceSlice','metadata':{'name':'slice','uid':'uid-a','resourceVersion':'opaque/y'},
         'spec':{'driver':'vendor.example','pool':{'name':'pool','generation':1,'resourceSliceCount':1},'nodeName':'node-a',
                 'devices':[{'name':'gpu-0','attributes':{'vendor.example/model':{'string':'H100'}}}]}}]}


def test_dra_preserves_attributes_uid_and_incomplete_pool():
    d=dra(); rows,issues=kubernetes(d,version='1.34.0')
    assert rows['slice']['uid']=='uid-a' and rows['slice']['attributes']==d['items'][0]
    assert 'pool_incomplete_or_generation_conflict' not in issues
    d['items'][0]['spec']['pool']['resourceSliceCount']=2
    assert 'pool_incomplete_or_generation_conflict' in kubernetes(d,version='1.34.0')[1]
    d['metadata']['continue']='next'
    with pytest.raises(ValueError): kubernetes(d,version='1.34.0')


def test_uid_replacement_invalidates_and_input_is_copied():
    e=event(); e.update(adapter='kubernetes-dra',producer_version='1.34.0',payload=dra())
    s=TopologyState('t','c'); s.apply(e); before=s.snapshot('2026-09-24T00:30:00Z')
    e['payload']['items'][0]['metadata']['uid']='uid-b'
    assert s.sources['graph']['records']['slice']['uid']=='uid-a'
    e.update(sequence=1,previous=s.sources['graph']['digest']);s.apply(e)
    assert changed_sources(before,s.snapshot('2026-09-24T00:30:00Z'))==['graph']


def test_cross_source_disagreement_stays_visible():
    s=TopologyState('t','c');s.apply(event());other=event();other['source']='other'
    other['payload']['instances'][0]['labels']['gpu']='B200';s.apply(other)
    snap=s.snapshot('2026-09-24T00:30:00Z')
    assert 'source_conflict' in snap['sources']['graph']['issues']
    assert 'source_conflict' in snap['sources']['other']['issues']
