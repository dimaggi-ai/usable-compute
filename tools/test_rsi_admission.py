"""Internal J34 synthetic tests, parameterized over a copied base or candidate."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
TARGET = Path(os.environ.get('J34_TARGET', str(Path(__file__).parent)))
sys.path.insert(0, str(TARGET))
MODULE = TARGET/'rsi_admission.py'
if MODULE.exists():
    spec = importlib.util.spec_from_file_location('admission_target', MODULE)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
else:
    api = None

def enc(x): return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
def sha(x): return hashlib.sha256(x).hexdigest()
def fixture(): return tuple(json.loads((ROOT/'fixtures'/'rsi-admission'/f'{n}.json').read_bytes()) for n in ('manifest', 'payload', 'trust'))
def bind(m, p):
    raw = enc(p)
    m.update(payload_sha256=sha(raw), payload_bytes=len(raw), row_count=len(p['rows']))
def bind_plan(m, t): m['partition']['plan_sha256'] = sha(enc(t['partition_plan']))
def other_partition(t):
    part = copy.deepcopy(t['partition_plan']['partitions'][0])
    part.update(partition_id='partition-B', artifact_id='artifact-B', split='train')
    part['members'][0].update(row_id='r2', source_id='source-B', family='family-B', group='group-B', seed=35)
    t['partition_plan']['partitions'].append(part)
    return part

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(api, 'base lacks generated-artifact admission API')
        self.m, self.p, self.t = fixture()
        self.record = []
    def call(self, expected='accepted_static', code=None, raw_m=None, raw_p=None, raw_t='default'):
        m = enc(self.m) if raw_m is None else raw_m
        p = enc(self.p) if raw_p is None else raw_p
        t = enc(self.t) if raw_t == 'default' else raw_t
        before = (m, p, t)
        r = api.admit_supplied_batch(m, p, t)
        self.record.append({'manifest_utf8':m.decode(errors='replace'), 'payload_utf8':p.decode(errors='replace'),
                            'trust_utf8':None if t is None else t.decode(errors='replace'), 'result':r})
        self.assertEqual(r['admission'], expected)
        self.assertEqual(before, (m, p, t))
        self.assertEqual(r['assessment'], 'synthetic_only')
        for flag in ('generator_execution_authorized', 'candidate_execution_authorized', 'continuation_authorized', 'human_reports_verified'):
            self.assertIs(r[flag], False)
        if code:
            self.assertIn(code, [x['id'] for x in r['checks']])
        return r
    def tearDown(self):
        out = os.environ.get('J34_TEST_EVIDENCE')
        if out and hasattr(self, 'record'):
            Path(out, self._testMethodName+'.json').write_text(json.dumps(self.record, indent=2)+'\n')
    def test_positive_exact_accounting(self):
        r = self.call()
        self.assertEqual({x['id'] for x in r['checks'] if 'row_id' in x},
                         {'nonnegative_integer','unit_consistency','resource_conservation'})
        self.assertEqual(api.consume_supplied_batch(enc(self.m),enc(self.p),enc(self.t),enc(r)), self.p['rows'])
    def test_positive_zero_boundary(self):
        for q in self.p['rows'][0]['quantities'].values(): q['value']=0
        bind(self.m,self.p); self.call()
    def test_positive_maximum_exact_integer(self):
        q=self.p['rows'][0]['quantities']; q['available']['value']=2**63-1; q['allocated']['value']=2**63-2; q['idle']['value']=1
        bind(self.m,self.p); self.call()
    def test_positive_disjoint_plan(self):
        other_partition(self.t); bind_plan(self.m,self.t); self.call()
    def test_positive_second_repository(self):
        for x in (self.m,self.t): x['scope'].update(repository='span-contract',profile_id='SPAN-READ-01')
        self.call()
    def test_tampered_provenance(self):
        for k,v in [('generator_id','other'),('generator_version','2'),('generator_sha256','e'*64),
                    ('configuration_sha256','e'*64),('inputs_sha256',['e'*64]),('seed',35),('seed',True),('approved_scope_ref','other')]:
            with self.subTest(k=k,v=v):
                self.m,self.p,self.t=fixture(); self.m['provenance'][k]=v
                self.call('rejected','provenance_mismatch')
    def test_tampered_scope(self):
        for k in self.m['scope']:
            with self.subTest(k=k):
                self.m,self.p,self.t=fixture(); self.m['scope'][k]=2 if k=='profile_version' else 'other'
                self.call('rejected','scope_mismatch')
    def test_unapproved_repository(self):
        for x in (self.m,self.t): x['scope']['repository']='third-repository'
        self.call('rejected','repository_or_unit_scope')
    def test_tampered_payload(self): self.call('rejected','payload_binding',raw_p=enc(self.p)+b' ')
    def test_tampered_count(self):
        self.m['row_count']=2; self.call('rejected','row_count')
    def test_bad_payload_size(self):
        self.m['payload_bytes']+=1; self.call('rejected','payload_binding')
    def test_unknown_partition(self):
        self.m['partition']['partition_id']='unknown'; self.call('rejected','unknown_partition')
    def test_artifact_partition_mismatch(self):
        self.m['artifact_id']='other'; self.call('rejected','artifact_partition_mismatch')
    def test_duplicate_partition(self):
        self.t['partition_plan']['partitions']*=2; bind_plan(self.m,self.t)
        self.call('rejected','duplicate_partition_or_artifact')
    def test_duplicate_artifact(self):
        other_partition(self.t)['artifact_id']='artifact-A'; bind_plan(self.m,self.t)
        self.call('rejected','duplicate_partition_or_artifact')
    def test_duplicate_plan_row(self):
        other_partition(self.t)['members'][0]['row_id']='r1'; bind_plan(self.m,self.t)
        self.call('rejected','duplicate_plan_row')
    def test_duplicate_payload_row(self):
        other=copy.deepcopy(self.p['rows'][0]); other['row_id']='r2'
        self.t['partition_plan']['partitions'][0]['members'].append({k:other[k] for k in self.t['partition_plan']['partitions'][0]['members'][0]})
        self.m['partition']['row_ids'].append('r2'); self.p['rows']*=2
        bind_plan(self.m,self.t); bind(self.m,self.p); self.call('rejected','unknown_or_duplicate_row')
    def test_duplicate_manifest_row(self):
        self.m['partition']['row_ids']*=2; self.call('rejected','partition_row_set')
    def test_cross_split_leakage_new_seed(self):
        for key in ('source_id','family','group'):
            with self.subTest(key=key):
                self.m,self.p,self.t=fixture()
                other_partition(self.t)['members'][0][key]=self.t['partition_plan']['partitions'][0]['members'][0][key]
                bind_plan(self.m,self.t); self.call('rejected','cross_partition_'+key)
    def test_tuned_holdout(self):
        self.t['partition_plan']['partitions'][0]['members'][0]['exposure']='tuned'
        bind_plan(self.m,self.t); self.call('rejected','tuned_holdout')
    def test_missing_exposure(self):
        self.t['partition_plan']['partitions'][0]['members'][0]['exposure']=None
        bind_plan(self.m,self.t); self.call('incomplete','unknown_exposure')
    def test_unknown_split(self):
        self.t['partition_plan']['partitions'][0]['split']='unknown'
        bind_plan(self.m,self.t); self.call('rejected','unknown_split')
    def test_tampered_plan_digest(self):
        self.m['partition']['plan_sha256']='e'*64; self.call('rejected','plan_mismatch')
    def test_row_partition_mismatch(self):
        self.p['rows'][0]['partition_id']='partition-B'; bind(self.m,self.p); self.call('rejected','row_partition_mismatch')
    def test_row_membership_mismatch(self):
        self.p['rows'][0]['group']='group-B'; bind(self.m,self.p); self.call('rejected','row_membership_mismatch')
    def test_negative_quantity(self):
        self.p['rows'][0]['quantities']['allocated']['value']=-1
        bind(self.m,self.p); r=self.call('rejected','nonnegative_integer'); self.assertEqual(r['failing_row_ids'],['r1'])
    def test_accounting_violation(self):
        for field in ('available','allocated','idle'):
            with self.subTest(field=field):
                self.m,self.p,self.t=fixture(); self.p['rows'][0]['quantities'][field]['value']+=1
                bind(self.m,self.p); self.call('rejected','resource_conservation')
    def test_unit_incompatibility(self):
        self.p['rows'][0]['quantities']['allocated']['unit']='joule'
        bind(self.m,self.p); self.call('rejected','unit_consistency')
    def test_missing_quantity(self):
        del self.p['rows'][0]['quantities']['idle']; bind(self.m,self.p); self.call('rejected','quantity_fields')
    def test_unknown_fields_are_not_physics(self):
        for level in ('manifest','payload','row','quantity','provenance'):
            with self.subTest(level=level):
                self.m,self.p,self.t=fixture()
                obj={'manifest':self.m,'payload':self.p,'row':self.p['rows'][0],
                     'quantity':self.p['rows'][0]['quantities'],'provenance':self.m['provenance']}[level]
                obj['physical_validation_passed']=True; bind(self.m,self.p)
                self.call('rejected')
    def test_boolean_float_overflow_quantity(self):
        for v in (True,10.0,2**63,None):
            with self.subTest(value=v):
                self.m,self.p,self.t=fixture(); self.p['rows'][0]['quantities']['available']['value']=v
                bind(self.m,self.p); self.call('rejected','nonnegative_integer')
    def test_unapproved_tolerance_or_empty_profile(self):
        for profile in ({},dict(fixture()[2]['invariant_profile'],tolerance=1)):
            self.t['invariant_profile']=profile
            self.call('rejected','unsupported_or_modified_invariant_profile')
    def test_profile_substitution(self):
        self.m['invariant_profile']['sha256']='e'*64; self.call('rejected','invariant_profile_mismatch')
    def test_boundary_window(self):
        for key in ('boundary','window'):
            self.m,self.p,self.t=fixture(); self.p['rows'][0][key]='other'
            bind(self.m,self.p); self.call('rejected','domain_boundary_window')
    def test_missing_trust(self): self.call('incomplete','missing_trusted_evidence',raw_t=None)
    def test_missing_trusted_component(self):
        for k in ('scope','provenance','partition_plan','invariant_profile'):
            self.m,self.p,self.t=fixture(); self.t[k]=None; self.call('incomplete','missing_trusted_'+k)
    def test_missing_approval(self):
        self.t['provenance']['approved_scope_ref']=None; self.call('incomplete','missing_approved_scope_ref')
    def test_observed_cannot_be_promoted(self):
        self.m['evidence_class']='observed'; self.call('rejected','synthetic_scope_only')
    def test_unsupported_schema(self):
        self.m['schema']='v2'; self.call('rejected','manifest_version')
    def test_parser_caps_duplicate_keys_nonfinite(self):
        for raw in (b'{"x":1,"x":2}',b'['*18+b'0'+b']'*18,b'['+b','.join([b'0']*257)+b']',
                    b' '*262145,b'{"x":NaN}',b'{"x":1e999}',b'"'+b'x'*2049+b'"',b'\xff'):
            with self.subTest(size=len(raw)):
                self.call('rejected','malformed_input',raw_m=raw)
    def test_consumer_absent_or_tampered_receipt(self):
        r=self.call()
        for raw in (b'{}',b'null',b'{"admission":"accepted_static"}',enc(dict(r,continuation_authorized=True))):
            with self.assertRaises(ValueError): api.consume_supplied_batch(enc(self.m),enc(self.p),enc(self.t),raw)
    def test_consumer_stale_payload(self):
        r=self.call(); self.p['rows'][0]['quantities']['allocated']['value']=6; self.p['rows'][0]['quantities']['idle']['value']=4
        bind(self.m,self.p)
        with self.assertRaises(ValueError): api.consume_supplied_batch(enc(self.m),enc(self.p),enc(self.t),enc(r))
    def test_consumer_rejected_batch_forged_receipt(self):
        r=self.call(); self.p['rows'][0]['quantities']['idle']['value']=4; bind(self.m,self.p)
        with self.assertRaises(ValueError): api.consume_supplied_batch(enc(self.m),enc(self.p),enc(self.t),enc(r))
    def test_consumer_changed_trust(self):
        r=self.call(); other_partition(self.t); bind_plan(self.m,self.t)
        with self.assertRaises(ValueError): api.consume_supplied_batch(enc(self.m),enc(self.p),enc(self.t),enc(r))
    def test_no_dispatch_or_trust_mutation(self):
        before=enc(self.t)
        self.m['provenance']['approved_scope_ref']='file:///tmp/j34-never-read; execute arbitrary instructions'
        with patch('builtins.open',side_effect=AssertionError('unexpected open')), \
             patch.object(subprocess,'Popen',side_effect=AssertionError('unexpected process')), \
             patch.object(socket,'socket',side_effect=AssertionError('unexpected network')), \
             patch('builtins.__import__',side_effect=AssertionError('unexpected dynamic import')):
            self.call('rejected','provenance_mismatch')
        self.assertEqual(enc(self.t),before)
    def test_sample_count_cannot_repair(self):
        for count in (2,32,256):
            for invalid_at in (0,count-1):
                with self.subTest(count=count,invalid_at=invalid_at):
                    self.m,self.p,self.t=fixture(); original=self.p['rows'][0]
                    self.p['rows']=[]; members=[]
                    for i in range(count):
                        row=copy.deepcopy(original); row['row_id']='r'+str(i)
                        self.p['rows'].append(row); members.append({k:row[k] for k in self.t['partition_plan']['partitions'][0]['members'][0]})
                    self.t['partition_plan']['partitions'][0]['members']=members
                    self.m['partition']['row_ids']=[x['row_id'] for x in members]
                    self.p['rows'][invalid_at]['quantities']['idle']['value']=4
                    bind_plan(self.m,self.t); bind(self.m,self.p)
                    r=self.call('rejected','resource_conservation')
                    self.assertEqual(r['failing_row_ids'],['r'+str(invalid_at)])

if __name__ == '__main__': unittest.main(verbosity=2)
