import pytest

from atomic_faults import inject_failure
from test_source_review_atomic import tool


@pytest.mark.parametrize('phase', ['write', 'fsync', 'replace'])
def test_fault_receipt_failure_preserves_previous(tmp_path, monkeypatch, phase):
    module = tool('local_transport_faults')
    target = tmp_path / 'receipt.json'
    target.write_bytes(b'previous receipt')
    events = inject_failure(monkeypatch, target, phase)
    with pytest.raises(OSError, match='injected'):
        module.save(target, {'status': 'passed'})
    assert events == [phase]
    assert target.read_bytes() == b'previous receipt'
    assert list(tmp_path.iterdir()) == [target]


def test_fault_receipt_stays_private(tmp_path):
    target = tmp_path / 'receipt.json'
    tool('local_transport_faults').save(target, {'status': 'complete'})
    assert target.stat().st_mode & 0o777 == 0o600
