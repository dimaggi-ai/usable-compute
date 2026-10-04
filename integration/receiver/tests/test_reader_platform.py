from types import SimpleNamespace
import pytest
from dimaggi_receiver import topology_watch as watch


def test_darwin_reader_refuses_kernel_publication_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, 'sys', SimpleNamespace(platform='darwin'))
    with pytest.raises(ValueError, match='kernel publication evidence unavailable'):
        watch.read_current(tmp_path/'watch.db', expiry_ledger=tmp_path/'reader.ledger',
                           tenant='t', cluster='c', collection='nodes',
                           now='2026-09-20T12:00:01Z')
