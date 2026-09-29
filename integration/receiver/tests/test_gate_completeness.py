import copy
import xml.etree.ElementTree as ET
import pytest
from test_ci_gate import gate, NAME, OLD
from test_gate_r4 import checker


def document(tmp_path, pin=OLD):
    result = gate(tmp_path, [(NAME, 'xfail' if pin == OLD else 'pass')], pin)
    assert result.returncode == 0, result.stderr
    path = tmp_path/'receiver-tests.xml'
    return path, ET.parse(path)


@pytest.mark.parametrize('pin', [OLD, 'f'*40])
def test_gate_requires_every_noncritical_case(tmp_path, pin):
    path, tree = document(tmp_path, pin)
    suite = next(tree.getroot().iter('testsuite'))
    case = ET.SubElement(suite, 'testcase', classname='tests.extra', name='test_unexpected')
    suite.set('tests', str(len(suite.findall('testcase'))))
    tree.write(path)
    with pytest.raises(ValueError): checker().check(path, tmp_path/'sources.lock.json')


@pytest.mark.parametrize('pin', [OLD, 'f'*40])
def test_gate_refuses_duplicate_simulator_identity(tmp_path, pin):
    path, tree = document(tmp_path, pin)
    suite = next(tree.getroot().iter('testsuite'))
    ET.SubElement(suite, 'testcase', classname='tests.test_sim_order', name=NAME)
    suite.set('tests', str(len(suite.findall('testcase'))))
    tree.write(path)
    with pytest.raises(ValueError): checker().check(path, tmp_path/'sources.lock.json')


@pytest.mark.parametrize('counter', ['tests', 'skipped', 'failures', 'errors'])
@pytest.mark.parametrize('location', ['suite', 'root'])
def test_gate_cross_checks_all_counters(tmp_path, counter, location):
    path, tree = document(tmp_path)
    node = tree.getroot() if location == 'root' else next(tree.getroot().iter('testsuite'))
    node.set(counter, '999')
    tree.write(path)
    with pytest.raises(ValueError): checker().check(path, tmp_path/'sources.lock.json')


def test_gate_refuses_critical_only_selection(tmp_path):
    path, tree = document(tmp_path)
    module = checker()
    import json
    from pathlib import Path
    critical = json.loads(Path(module.__file__).with_name('critical_tests.json').read_text())
    suite = next(tree.getroot().iter('testsuite'))
    for case in list(suite.findall('testcase')):
        if [case.get('classname'), case.get('name')] not in critical:
            suite.remove(case)
    suite.set('tests', str(len(suite.findall('testcase'))))
    tree.write(path)
    with pytest.raises(ValueError): module.check(path, tmp_path/'sources.lock.json')


def test_ci_manifest_matches_current_collection():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1]/'tools/ci_test_manifest.py'
    spec = importlib.util.spec_from_file_location('ci_manifest', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    import json
    assert module.collect('receiver') == json.loads(module.manifest_path('receiver').read_text())


@pytest.mark.parametrize('mutation', ['remove', 'duplicate'])
def test_noncritical_manifest_case_required_exactly_once(tmp_path, mutation):
    path, tree = document(tmp_path)
    suite = next(tree.getroot().iter('testsuite'))
    case = next(c for c in suite.findall('testcase') if c.get('classname') == 'tests.test_watch_current')
    if mutation == 'remove': suite.remove(case)
    else: suite.append(copy.deepcopy(case))
    suite.set('tests', str(len(suite.findall('testcase'))))
    tree.write(path)
    with pytest.raises(ValueError, match='manifest'): checker().check(path, tmp_path/'sources.lock.json')


@pytest.mark.parametrize('mutation', ['none', 'remove', 'duplicate', 'extra', 'skip'])
def test_tools_gate_requires_complete_passing_manifest(tmp_path, mutation):
    import json
    from pathlib import Path
    module = checker()
    manifest_file = Path(module.__file__).with_name('tools_tests.json')
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', failures='0', errors='0', skipped='0')
    ET.SubElement(ET.SubElement(suite, 'properties'), 'property', name='receiver_xfail_policy', value='strict-v1')
    for node in json.loads(manifest_file.read_text()):
        address, bracket, param = node.partition('[')
        parts = address.split('::')
        classname = '.'.join([parts[0].removesuffix('.py').replace('/', '.'), *parts[1:-1]])
        ET.SubElement(suite, 'testcase', classname=classname, name=parts[-1]+bracket+param)
    case = suite.find('testcase')
    if mutation == 'remove': suite.remove(case)
    if mutation == 'duplicate': suite.append(copy.deepcopy(case))
    if mutation == 'extra': ET.SubElement(suite, 'testcase', classname='tools.extra', name='test_extra')
    if mutation == 'skip':
        ET.SubElement(case, 'skipped', type='pytest.xfail'); suite.set('skipped', '1')
    suite.set('tests', str(len(suite.findall('testcase'))))
    xml = tmp_path/'tools.xml'; ET.ElementTree(root).write(xml)
    lock = Path(module.__file__).parent.parent/'src/dimaggi_receiver/sources.lock.json'
    if mutation == 'none': module.check(xml, lock, 'tools')
    else:
        with pytest.raises(ValueError): module.check(xml, lock, 'tools')
