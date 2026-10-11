import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from atomic_faults import inject_failure
from dimaggi_receiver.jsonio import loads
from test_source_watch import CONFIG


def tool(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('phase', ['write', 'fsync', 'replace'])
@pytest.mark.parametrize('filename', ['snapshot.json', 'queue.json', 'report.json', 'review-evidence.txt', 'source'])
def test_review_write_failure_preserves_history(tmp_path, monkeypatch, phase, filename):
    runner, reviewer = tool('source_watch_run'), tool('source_watch_review')
    history = tmp_path / 'history'
    history.mkdir()
    runner.run(CONFIG, history, lambda *a, **k: (b'old', 'text/html'))
    result, _ = runner.run(CONFIG, history, lambda *a, **k: (b'new', 'text/html'))
    queue = loads(Path(result['run'], 'queue.json').read_text())
    evidence = tmp_path / 'evidence.txt'
    evidence.write_bytes(b'synthetic review evidence')
    previous = {p: p.read_bytes() for p in history.rglob('*') if p.is_file()}
    monkeypatch.setattr(reviewer, 'datetime', SimpleNamespace(now=lambda *a: SimpleNamespace(strftime=lambda *a: '9999')))
    output = history / '9999-review'
    if filename == 'source':
        filename = 'sources/' + next(Path(result['run'], 'sources').iterdir()).name
    target = output / filename
    events = inject_failure(monkeypatch, target, phase)
    with pytest.raises(OSError, match='injected'):
        reviewer.review(history, queue['queue_digest'], [queue['findings'][0]['finding_id']], evidence, 'test-reviewer')
    assert events == [phase]
    assert all(p.read_bytes() == raw for p, raw in previous.items())
    assert not target.exists()
    assert not list(output.rglob('.*'))
    assert not (output / 'report.json').exists()
