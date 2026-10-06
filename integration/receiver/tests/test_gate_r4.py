import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest
from test_ci_gate import gate, NAME

ROOT = Path(__file__).resolve().parents[3]


def checker():
    spec = importlib.util.spec_from_file_location('acceptance_gate', ROOT/'integration/receiver/tools/check_acceptance.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_gate_refuses_missing_regression_after_repin(tmp_path):
    assert gate(tmp_path, [('unrelated', 'pass')], 'f'*40).returncode != 0


@pytest.mark.parametrize('marker', ['attribute', 'property', 'output'])
def test_gate_refuses_xpass_representations(tmp_path, marker):
    gate(tmp_path, [(NAME, 'pass')], 'f'*40)
    path = tmp_path/'receiver-tests.xml'
    tree = ET.parse(path); case = next(tree.getroot().iter('testcase'))
    if marker == 'attribute': case.set('wasxfail', 'reason')
    elif marker == 'property': ET.SubElement(ET.SubElement(case, 'properties'), 'property', name='wasxfail', value='reason')
    else: ET.SubElement(case, 'system-out').text = 'XPASS: unexpected pass'
    tree.write(path)
    with pytest.raises(ValueError): checker().check(path, tmp_path/'sources.lock.json')


def test_gate_refuses_unmarked_junit_from_unenforced_runner(tmp_path):
    gate(tmp_path, [(NAME, 'pass')], 'f'*40)
    path = tmp_path/'receiver-tests.xml'; tree = ET.parse(path)
    for suite in tree.getroot().iter('testsuite'):
        for props in suite.findall('properties'): suite.remove(props)
    tree.write(path)
    with pytest.raises(ValueError): checker().check(path, tmp_path/'sources.lock.json')


@pytest.mark.parametrize('strict', ['', ', strict=False'])
def test_actual_pytest_xpass_fails_even_with_marker_override(tmp_path, strict):
    import subprocess
    import sys
    import shutil
    shutil.copyfile(ROOT/'integration/receiver/tests/conftest.py', tmp_path/'conftest.py')
    (tmp_path/'pytest.ini').write_text('[pytest]\nxfail_strict=true\n')
    (tmp_path/'test_probe.py').write_text('import pytest\n@pytest.mark.xfail(reason="injected"'+strict+')\ndef test_unexpected_pass(): pass\n')
    result = subprocess.run([sys.executable, '-m', 'pytest', '-q', str(tmp_path), '--junitxml', str(tmp_path/'out.xml')], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'XPASS' in result.stdout


def test_every_declared_critical_test_is_required(tmp_path):
    critical = json.loads((ROOT/'integration/receiver/tools/critical_tests.json').read_text())
    for classname, name in critical:
        gate(tmp_path, [(NAME, 'pass')], 'f'*40)
        path = tmp_path/'receiver-tests.xml'; tree = ET.parse(path)
        suite = next(tree.getroot().iter('testsuite'))
        case = next(c for c in suite.findall('testcase') if (c.get('classname'), c.get('name')) == (classname, name))
        suite.remove(case); suite.set('tests', str(len(suite.findall('testcase')))); tree.write(path)
        with pytest.raises(ValueError, match='required receiver regression'): checker().check(path, tmp_path/'sources.lock.json')


def test_gate_distinguishes_test_identity_from_xpass_result(tmp_path):
    from test_ci_gate import OLD
    result = gate(tmp_path, [(NAME, 'xfail')], OLD)
    assert result.returncode == 0, result.stderr


def test_critical_tests_are_required_at_the_old_pin(tmp_path):
    from test_ci_gate import OLD
    critical = json.loads((ROOT/'integration/receiver/tools/critical_tests.json').read_text())
    for classname, name in critical:
        if name == NAME:
            continue
        gate(tmp_path, [(NAME, 'xfail')], OLD)
        path = tmp_path/'receiver-tests.xml'; tree = ET.parse(path)
        suite = next(tree.getroot().iter('testsuite'))
        case = next(c for c in suite.findall('testcase') if (c.get('classname'), c.get('name')) == (classname, name))
        suite.remove(case); suite.set('tests', str(len(suite.findall('testcase')))); tree.write(path)
        with pytest.raises(ValueError, match='required receiver regression'): checker().check(path, tmp_path/'sources.lock.json')
