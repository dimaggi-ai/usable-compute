from types import SimpleNamespace
from pathlib import Path
import runpy
import pytest
from dimaggi_receiver import topology_watch as watch


def test_darwin_reader_refuses_kernel_publication_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(watch, 'sys', SimpleNamespace(platform='darwin'))
    with pytest.raises(ValueError, match='kernel publication evidence unavailable'):
        watch.read_current(tmp_path/'watch.db', expiry_ledger=tmp_path/'reader.ledger',
                           tenant='t', cluster='c', collection='nodes',
                           now='2026-09-20T12:00:01Z')


def test_infrastructure_example_documents_linux_requirement(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    example = runpy.run_path(str(root / 'examples/infrastructure/generate.py'))
    generate = example['generate']
    monkeypatch.setitem(generate.__globals__, '__file__', str(tmp_path / 'generate.py'))
    monkeypatch.setattr(watch, 'sys', SimpleNamespace(platform='darwin'))
    with pytest.raises(ValueError, match='kernel publication evidence unavailable'):
        generate()
    assert not list(tmp_path.glob('*.json'))
    instructions = (root / 'INFRASTRUCTURE.md').read_text().split('## Reproduce locally\n', 1)[1]
    assert 'Generating the infrastructure examples requires Linux' in instructions
    assert 'kernel publication evidence' in instructions
