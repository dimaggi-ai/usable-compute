"""Check receiver JUnit results with one source-pin-specific expected failure."""
import json
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
    suites = list(root.iter('testsuite'))
    if any(int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in suites):
        raise ValueError('failed receiver acceptance suite')
    skips = [(case, skip) for case in cases for skip in case.findall('skipped')]
    pin = json.loads(Path(lock_path).read_text())['repositories']['reliability-economics']['commit']
    expected = int(pin == OLD_SIMULATOR)
    if len(skips) != expected or sum(int(s.get('skipped', 0)) for s in suites) != len(skips):
        raise ValueError('unexpected skipped receiver acceptance test')
    if expected:
        case, skip = skips[0]
        if (case.get('classname') != KNOWN_CLASS or case.get('name') != KNOWN_TEST
                or skip.get('type') != 'pytest.xfail'
                or skip.get('message') != 'METRICS-05: source re-pin is phase 2'):
            raise ValueError('unexpected receiver acceptance exception')
    print(f'Acceptance gate passed: {len(cases)} cases, {expected} known expected failure')


if __name__ == '__main__':
    check(*sys.argv[1:])
