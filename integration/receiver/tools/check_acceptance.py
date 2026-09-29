"""Check receiver JUnit results with one source-pin-specific expected failure."""
import json
import re
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

OLD_SIMULATOR = '3b7b253e6b8c0987e9923fe74d483455715cd307'
KNOWN_CLASS = 'tests.test_sim_order'
KNOWN_TEST = 'test_direct_sim_normalizes_or_refuses_order_reversal'


def check(xml_path, lock_path):
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
    if any(int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in suites):
        raise ValueError('failed receiver acceptance suite')
    skips = [(case, skip) for case in cases for skip in case.findall('skipped')]
    pin = json.loads(Path(lock_path).read_text())['repositories']['reliability-economics']['commit']
    expected = int(pin == OLD_SIMULATOR)
    if len(skips) != expected or sum(int(s.get('skipped', 0)) for s in suites) != len(skips):
        raise ValueError('unexpected skipped receiver acceptance test')
    if not expected:
        critical = json.loads(Path(__file__).with_name('critical_tests.json').read_text())
        for classname, name in critical:
            matches = [case for case in cases if (case.get('classname'), case.get('name')) == (classname, name)]
            if len(matches) != 1 or any(matches[0].find(tag) is not None for tag in ('skipped', 'error', 'failure')):
                raise ValueError('required receiver regression missing or not passing: ' + classname + '::' + name)
    if expected:
        case, skip = skips[0]
        if (case.get('classname') != KNOWN_CLASS or case.get('name') != KNOWN_TEST
                or skip.get('type') != 'pytest.xfail'
                or skip.get('message') != 'METRICS-05: source re-pin is phase 2'):
            raise ValueError('unexpected receiver acceptance exception')
    print(f'Acceptance gate passed: {len(cases)} cases, {expected} known expected failure')


if __name__ == '__main__':
    check(*sys.argv[1:])
