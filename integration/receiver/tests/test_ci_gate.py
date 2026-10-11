import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
LOCK = ROOT/'integration/receiver/src/dimaggi_receiver/sources.lock.json'
OLD = '3b7b253e6b8c0987e9923fe74d483455715cd307'
NAME = 'test_direct_sim_normalizes_or_refuses_order_reversal'


def gate(tmp_path, cases, pin=OLD):
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', tests=str(len(cases)), skipped=str(sum(c[1] in ('skip','xfail') for c in cases)), failures=str(sum(c[1]=='failure' for c in cases)), errors='0')
    props = ET.SubElement(suite, 'properties')
    ET.SubElement(props, 'property', name='receiver_xfail_policy', value='strict-v1')
    if pin == OLD or (NAME, 'pass') in cases:
        import importlib.util
        spec = importlib.util.spec_from_file_location('ci_manifest', ROOT/'integration/receiver/tools/ci_test_manifest.py')
        manifest = importlib.util.module_from_spec(spec); spec.loader.exec_module(manifest)
        for nodeid in json.loads(manifest.manifest_path('receiver').read_text()):
            classname, name = manifest.junit_identity(nodeid)
            if (classname, name) != ('tests.test_sim_order', NAME):
                ET.SubElement(suite, 'testcase', classname=classname, name=name)
    for name, outcome in cases:
        case = ET.SubElement(suite, 'testcase', classname='tests.test_sim_order', name=name)
        if outcome != 'pass':
            ET.SubElement(case, 'failure' if outcome=='failure' else 'skipped',
                          type='pytest.xfail' if outcome=='xfail' else 'pytest.skip',
                          message='METRICS-05: source re-pin is phase 2')
    suite.set('tests', str(len(suite.findall('testcase'))))
    xml = tmp_path/'receiver-tests.xml'; ET.ElementTree(root).write(xml)
    lock = json.loads(LOCK.read_text()); lock['repositories']['reliability-economics']['commit']=pin
    lock_path=tmp_path/'sources.lock.json'; lock_path.write_text(json.dumps(lock))
    script=ROOT/'integration/receiver/tools/check_acceptance.py'
    if script.exists(): command=[sys.executable, str(script), str(xml), str(lock_path)]
    else:
        # Run the round-one workflow's actual inline gate, not a missing new API.
        steps=yaml.safe_load((ROOT/'.github/workflows/receiver.yml').read_text())['jobs']['receiver']['steps']
        step=next(s['run'] for s in steps if s.get('name')=='Installed receiver and source-bound domain tests')
        code=step.split("python - <<'PY'\n")[1].rsplit('\nPY',1)[0]
        command=[sys.executable,'-c',code]
    return subprocess.run(command,env=dict(os.environ,RUNNER_TEMP=str(tmp_path)),capture_output=True,text=True,timeout=5)


def test_gate_accepts_exact_known_old_pin_xfail(tmp_path):
    result=gate(tmp_path,[(NAME,'xfail')])
    assert result.returncode==0, result.stderr


@pytest.mark.parametrize('cases,pin', [
    ([(NAME,'skip')],OLD), ([('other','xfail')],OLD), ([(NAME,'xfail')]*2,OLD),
    ([(NAME,'xfail'),('other','skip')],OLD), ([(NAME,'xfail'),('other','failure')],OLD),
    ([(NAME,'xfail')],'f'*40), ([(NAME,'pass')],OLD), ([],OLD)])
def test_gate_refuses_every_other_exception(tmp_path,cases,pin):
    assert gate(tmp_path,cases,pin).returncode != 0


def test_gate_requires_no_exception_after_repin(tmp_path):
    assert gate(tmp_path,[(NAME,'pass')], 'f'*40).returncode==0
