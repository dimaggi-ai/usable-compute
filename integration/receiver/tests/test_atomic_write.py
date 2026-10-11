import os
from pathlib import Path
import stat

import pytest

from atomic_faults import inject_failure
from dimaggi_receiver.atomic import atomic_write


@pytest.mark.parametrize('phase', ['write', 'fsync', 'replace'])
def test_atomic_write_preserves_previous(tmp_path, monkeypatch, phase):
    target = tmp_path / 'receipt.json'
    target.write_bytes(b'previous')
    events = inject_failure(monkeypatch, target, phase)
    with pytest.raises(OSError, match='injected'):
        atomic_write(target, b'{"status":"passed"}')
    assert events == [phase]
    assert target.read_bytes() == b'previous'
    assert list(tmp_path.iterdir()) == [target]


def test_directory_sync_failure_raises_after_complete_replace(tmp_path, monkeypatch):
    target = tmp_path / 'receipt.json'
    target.write_bytes(b'previous')
    events = inject_failure(monkeypatch, target, 'directory_fsync')
    with pytest.raises(OSError, match='injected directory fsync'):
        atomic_write(target, b'{"status":"passed"}')
    assert events == ['directory_fsync']
    assert target.read_bytes() == b'{"status":"passed"}'
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_syncs_file_then_replaces_then_syncs_directory(tmp_path, monkeypatch):
    target = tmp_path / 'receipt.json'
    events = []
    sync, replace = os.fsync, os.replace
    def synced(fd):
        events.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file')
        return sync(fd)
    def replaced(source, destination):
        assert Path(source).parent == target.parent
        assert Path(source).read_bytes() == b'complete'
        assert not Path(source).stat().st_mode & 0o077
        events.append('replace')
        return replace(source, destination)
    monkeypatch.setattr(os, 'fsync', synced)
    monkeypatch.setattr(os, 'replace', replaced)
    atomic_write(target, b'complete')
    assert events == ['file', 'replace', 'directory']
    assert list(tmp_path.iterdir()) == [target]
