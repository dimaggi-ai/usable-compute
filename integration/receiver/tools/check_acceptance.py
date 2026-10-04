"""Check the named CI selection, with one receiver pin-specific exception."""
from collections import Counter
import json
import re
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

OLD_SIMULATOR = '3b7b253e6b8c0987e9923fe74d483455715cd307'
KNOWN_CLASS = 'tests.test_sim_order'
KNOWN_TEST = 'test_direct_sim_normalizes_or_refuses_order_reversal'


def check(xml_path, lock_path, suite="receiver"):
    if suite not in {"receiver", "tools", "macos_compatibility"}:
        raise ValueError("unknown acceptance selection")
    root = ET.parse(xml_path).getroot()
    cases = list(root.iter('testcase'))
    if not cases or list(root.iter('failure')) or list(root.iter('error')):
        raise ValueError('empty or failed receiver acceptance suite')
    for element in root.iter():
        identity = {'name', 'classname', 'file'} if element.tag in ('testcase', 'testsuite', 'testsuites') else set()
        fields = [element.tag, element.text or '', element.tail or '', *element.attrib.keys(),
                  *(value for key, value in element.attrib.items() if key not in identity)]
        if any(re.search(r'wasxfail|xpass', field, re.I) for field in fields):
            raise ValueError('unexpected xfail or XPASS representation')
    suites = list(root.iter('testsuite'))
    if not suites or any(not any(p.get('name') == 'receiver_xfail_policy' and p.get('value') == 'strict-v1'
                                for p in suite.findall('./properties/property')) for suite in suites):
        raise ValueError('receiver xfail enforcement evidence required')
    for element in root.iter():
        if element.tag not in ('testsuite', 'testsuites'):
            continue
        descendants = list(element.iter('testcase'))
        actual = {'tests': len(descendants),
                  **{name: sum(len(case.findall(tag)) for case in descendants)
                     for name, tag in [('skipped', 'skipped'), ('failures', 'failure'), ('errors', 'error')]}}
        for name, count in actual.items():
            raw = element.get(name)
            if raw is None and element.tag == 'testsuites':
                continue
            if raw is None or not raw.isascii() or not raw.isdigit() or int(raw) != count:
                raise ValueError('JUnit suite counter mismatch: ' + name)
    skips = [(case, skip) for case in cases for skip in case.findall('skipped')]
    pin = json.loads(Path(lock_path).read_text())['repositories']['reliability-economics']['commit']
    expected = int(suite == "receiver" and pin == OLD_SIMULATOR)
    if len(skips) != expected or sum(int(s.get('skipped', 0)) for s in suites) != len(skips):
        raise ValueError('unexpected skipped receiver acceptance test')
    # Critical regressions are required at every pin. At the old simulator pin the
    # simulator regression is instead the single known expected failure checked below.
    critical = json.loads(Path(__file__).with_name('critical_tests.json').read_text()) if suite == 'receiver' else []
    for classname, name in critical:
        matches = [case for case in cases if (case.get('classname'), case.get('name')) == (classname, name)]
        allowed_xfail = expected and (classname, name) == (KNOWN_CLASS, KNOWN_TEST)
        if len(matches) != 1 or (not allowed_xfail and any(matches[0].find(tag) is not None for tag in ('skipped', 'error', 'failure'))):
            raise ValueError('required receiver regression missing or not passing: ' + classname + '::' + name)
    if expected:
        case, skip = skips[0]
        if (case.get('classname') != KNOWN_CLASS or case.get('name') != KNOWN_TEST
                or skip.get('type') != 'pytest.xfail'
                or skip.get('message') != 'METRICS-05: source re-pin is phase 2'):
            raise ValueError('unexpected receiver acceptance exception')
    # Compare identities, not just a count or a critical subset.
    import importlib.util
    spec = importlib.util.spec_from_file_location('ci_test_manifest', Path(__file__).with_name('ci_test_manifest.py'))
    manifest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manifest)
    junit_identity, manifest_path = manifest.junit_identity, manifest.manifest_path
    nodes = json.loads(manifest_path(suite).read_text())
    required = Counter(junit_identity(node) for node in nodes)
    actual = Counter((case.get('classname'), case.get('name')) for case in cases)
    if not required or any(count != 1 for count in required.values()) or actual != required:
        raise ValueError('JUnit case set differs from CI manifest')
    print(f'{suite} CI gate passed: {len(cases)} cases, {expected} known expected failure')


if __name__ == '__main__':
    check(*sys.argv[1:])
