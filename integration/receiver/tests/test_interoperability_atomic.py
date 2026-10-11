import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_faults import inject_failure
from test_source_review_atomic import tool


@pytest.mark.parametrize('phase', ['write', 'fsync', 'replace'])
@pytest.mark.parametrize('command_failed', [False, True])
def test_interoperability_manifest_failure_preserves_previous(tmp_path, monkeypatch, phase, command_failed, capsys):
    module = tool('verify_interoperability')
    tenwa = tmp_path / 'tenwa'
    executor = tenwa / 'internal/batchexecutor/executor.go'
    executor.parent.mkdir(parents=True)
    executor.touch()
    output = tmp_path / 'output'
    target = output / 'manifest.json'
    # Seed a prior artifact at directory creation to exercise replacement too.
    mkdir = Path.mkdir
    def make_directory(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if path == output:
            target.write_bytes(b'previous manifest')
        return result
    monkeypatch.setattr(Path, 'mkdir', make_directory)
    monkeypatch.setattr(module.sys, 'argv', ['verify', '--tenwa-root', str(tenwa), '--sources', str(tmp_path), '--output', str(output)])
    monkeypatch.setattr(module, 'verify_installed', lambda root: {'synthetic.py': 'fixture'})
    from dimaggi_receiver import sources
    monkeypatch.setattr(sources, 'verify_bundle', lambda path: None)
    def command(argv, **kwargs):
        if '--junitxml' in argv:
            Path(argv[argv.index('--junitxml') + 1]).write_text('<testsuites><testsuite tests="1"/></testsuites>')
        return SimpleNamespace(returncode=1 if command_failed else 0)
    monkeypatch.setattr(module.subprocess, 'run', command)
    # Delay injection until setup has seeded the old file.
    original_mkdir = Path.mkdir
    events = []
    def inject_on_bin(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == output / 'bin':
            events.append(inject_failure(monkeypatch, target, phase))
        return result
    monkeypatch.setattr(Path, 'mkdir', inject_on_bin)
    with pytest.raises(OSError, match='injected'):
        module.main()
    assert events == [[phase]]
    assert target.read_bytes() == b'previous manifest'
    assert not list(output.glob('.*'))
    assert not capsys.readouterr().out
