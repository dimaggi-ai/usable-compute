import importlib.util
import json
from pathlib import Path
import shutil

import pytest
from dimaggi_receiver.expiry_ledger import check_generation
from test_reader_ledger import state, read
from test_gate_completeness import document
from test_gate_r4 import checker


@pytest.mark.parametrize('change', ['truncate', 'substitute', 'recreate'])
def test_ledger_history_loss_is_not_detectable(state, change):
    store, path, ledger, start = state
    # Keep an unrelated, valid record to test loss of a suffix, not an empty file.
    with pytest.raises(ValueError):
        check_generation(ledger, 'other-store', ['t', 'c', 'nodes', ''], 'other', False)
    earlier = ledger.read_bytes()
    identity = store.db.execute('SELECT identity FROM store_identity').fetchone()[0]
    generation = store.db.execute('SELECT generation FROM lease').fetchone()[0]
    with pytest.raises(ValueError):
        check_generation(ledger, identity, ['t', 'c', 'nodes', ''], generation, False)
    with pytest.raises(ValueError, match='previously expired'): read(path, ledger)
    if change == 'substitute':
        other = ledger.with_suffix('.replacement')
        other.write_bytes(earlier); other.chmod(0o600); other.replace(ledger)
    else:
        if change == 'recreate': ledger.unlink()
        ledger.write_bytes(earlier); ledger.chmod(0o600)
    assert not read(path, ledger)['issues']


def test_reviewed_manifest_is_the_completeness_boundary(tmp_path):
    xml, tree = document(tmp_path)
    source = Path(checker().__file__).parent
    copied = tmp_path/'gate'; copied.mkdir()
    for name in ['check_acceptance.py', 'ci_test_manifest.py', 'receiver_tests.json', 'critical_tests.json']:
        shutil.copy(source/name, copied/name)
    spec = importlib.util.spec_from_file_location('copied_gate', copied/'check_acceptance.py')
    gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(gate)
    nodes = json.loads((copied/'receiver_tests.json').read_text())
    removed = [n for n in nodes if '/test_reader_future.py::' in n][:2]
    assert len(removed) == 2
    names = {n.split('::')[-1] for n in removed}
    suite = next(tree.getroot().iter('testsuite'))
    for case in list(suite.findall('testcase')):
        if case.get('classname') == 'tests.test_reader_future' and case.get('name') in names:
            suite.remove(case)
    suite.set('tests', str(len(suite.findall('testcase')))); tree.write(xml)
    with pytest.raises(ValueError, match='manifest'): gate.check(xml, tmp_path/'sources.lock.json')
    (copied/'receiver_tests.json').write_text(json.dumps([n for n in nodes if n not in removed]))
    gate.check(xml, tmp_path/'sources.lock.json')
