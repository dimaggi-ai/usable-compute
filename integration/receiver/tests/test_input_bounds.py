"""Exercise refusal before unbounded allocation or domain evaluation."""
import io
import json
import os

import pytest

from dimaggi_receiver import cli, jsonio


class BudgetStream:
    def __init__(self):
        self.requested = None

    def read(self, count=-1):
        assert count == jsonio.MAX_BYTES + 1
        self.requested = count
        return b' ' * count


def test_stream_reads_only_limit_plus_one():
    stream = BudgetStream()
    with pytest.raises(ValueError, match='4 MiB'):
        jsonio.read_stream(stream)
    assert stream.requested == jsonio.MAX_BYTES + 1


def test_exact_limit_and_utf8_bytes():
    raw = b'"' + 'é'.encode() * ((jsonio.MAX_BYTES - 2) // 2) + b'"'
    assert len(raw) == jsonio.MAX_BYTES
    assert jsonio.read_stream(io.BytesIO(raw)) == raw


@pytest.mark.parametrize('command', ['evaluate', 'policy-input'])
def test_oversize_file_refuses_before_domain(command, tmp_path, monkeypatch, capsys):
    path = tmp_path / 'oversize.json'
    with path.open('wb') as stream:
        stream.truncate(jsonio.MAX_BYTES + 1)
    def must_not_run(*args):
        pytest.fail('domain evaluation must not run for oversized input')
    monkeypatch.setattr(cli, 'evaluate', must_not_run)
    monkeypatch.setattr(cli, 'policy_input', must_not_run)
    args = (['evaluate', '--sources', '/unused', '--input', str(path)] if command == 'evaluate'
            else ['policy-input', '--report', str(path), '--request-id', 'bounded-1'])
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert '4 MiB' in result['message']
    assert result['execution_authorized'] is False


def test_fifo_refuses_without_waiting_for_writer(tmp_path):
    path = tmp_path / 'fifo'
    os.mkfifo(path)
    with pytest.raises(ValueError, match='regular file'):
        jsonio.read_file(path)


def test_stdin_oversize_structured_error(monkeypatch, capsys):
    monkeypatch.setattr('sys.stdin', io.TextIOWrapper(io.BytesIO(b' ' * (jsonio.MAX_BYTES + 1))))
    assert cli.main(['evaluate', '--sources', '/unused']) == 2
    assert '4 MiB' in json.loads(capsys.readouterr().out)['message']
