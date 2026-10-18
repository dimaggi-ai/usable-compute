import dataclasses
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time

import pytest

from dimaggi_receiver.effect_observation import (
    MAX_BYTES, EffectReceipt, Observation, collect, parse_snapshot, verify,
)


def receipt(operation='request/1', payload=b'input', destination='sink'):
    return EffectReceipt(operation, hashlib.sha256(payload).hexdigest(), destination)


def encoded(*effects):
    return json.dumps({'schema_version': 'effect-observation/v1',
                       'effects': [dataclasses.asdict(item) for item in effects]}).encode()


def test_positive_and_empty_controls():
    first, second = receipt(), receipt('request/2')
    observed = parse_snapshot(encoded(second, first))
    expected = Observation((first, second))
    result = verify(observed, expected, checkpoint=Observation((first,)))
    assert result.complete
    assert result.observed_effects == 2
    assert result.findings == ()
    assert verify(parse_snapshot(encoded()), Observation(())).complete
    assert not verify(parse_snapshot(encoded()), expected).complete


@pytest.mark.parametrize('changed', [receipt(payload=b'changed'), receipt(destination='other')])
def test_modification(changed):
    expected = Observation((receipt(),))
    result = verify(Observation((changed,)), expected, checkpoint=expected)
    assert result.findings == ('checkpoint_modification:request/1', 'modified_effect:request/1')


def test_omission_truncation_replacement_and_unexpected_effect():
    first, second = receipt(), receipt('request/2')
    checkpoint = Observation((first, second))
    for remaining in ((), (first,)):
        snapshot = Observation(remaining)
        # Even replacing expected evidence with the same truncated data fails.
        result = verify(snapshot, snapshot, checkpoint=checkpoint)
        assert not result.complete
        assert 'checkpoint_omission:request/2' in result.findings
    assert verify(Observation((first, second)), Observation((first,))).findings == ('unexpected_effect:request/2',)


@pytest.mark.parametrize('raw', [
    b'{', b'\xff', b'null',
    b'{"schema_version":"effect-observation/v1","effects":[],"effects":[]}',
    b'{"schema_version":"unknown","effects":[]}',
    b'{"schema_version":"effect-observation/v1","effects":null}',
    b'{"schema_version":"effect-observation/v1","effects":[],"extra":1}',
    b'{"schema_version":"effect-observation/v1","effects":[{}]}',
    b'x' * (MAX_BYTES + 1),
    encoded(receipt(), receipt()),
    encoded(dataclasses.replace(receipt(), input_sha256='not-a-hash')),
    encoded(dataclasses.replace(receipt(), operation_id='')),
    encoded(dataclasses.replace(receipt(), destination=True)),
])
def test_malformed_snapshot(raw):
    with pytest.raises(ValueError):
        parse_snapshot(raw)


def test_expected_and_checkpoint_are_validated():
    invalid = Observation((receipt(), receipt()))
    with pytest.raises(ValueError, match='duplicate_operation'):
        verify(Observation(()), invalid)
    with pytest.raises(ValueError, match='duplicate_operation'):
        verify(Observation(()), Observation(()), checkpoint=invalid)


@pytest.fixture
def endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path, dict(self.headers)))
            self.send_response(self.server.response_status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(self.server.body) + self.server.extra_length))
            self.end_headers()
            time.sleep(self.server.body_delay)
            try:
                self.wfile.write(self.server.body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.extra_length = 0
    server.body_delay = 0
    server.response_status = 200
    server.body = encoded(receipt())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/observations', server, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


def test_collection_is_read_only_and_ignores_proxy_environment(endpoint, monkeypatch):
    url, server, requests = endpoint
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    observed = collect(url)
    assert verify(observed, Observation((receipt(),))).complete
    assert [(method, path) for method, path, _ in requests] == [('GET', '/observations')]
    assert 'Authorization' not in requests[0][2]


@pytest.mark.parametrize('status', [302, 401, 500])
def test_http_failure_and_redirect_refused(endpoint, status):
    url, server, requests = endpoint
    server.response_status = status
    with pytest.raises(ValueError, match='observation_http_status'):
        collect(url)
    assert len(requests) == 1


def test_response_bound(endpoint):
    url, server, _ = endpoint
    server.body = b'x' * (MAX_BYTES + 1)
    with pytest.raises(ValueError, match='invalid_snapshot_size'):
        collect(url)


@pytest.mark.parametrize('url', [
    'https://127.0.0.1/observations', 'http://localhost/observations',
    'http://192.0.2.1/observations', 'http://127.0.0.1/write',
    'http://secret@127.0.0.1/observations', 'http://127.0.0.1/observations?token=secret',
    'http://127.0.0.1/observations#fragment',
])
def test_endpoint_boundaries(url):
    with pytest.raises(ValueError):
        collect(url)


@pytest.mark.parametrize('timeout', [0, -1, 31, float('nan'), float('inf'), True])
def test_timeout_boundaries(timeout):
    with pytest.raises(ValueError, match='invalid_timeout'):
        collect('http://127.0.0.1/observations', timeout=timeout)


def test_unavailable_sink():
    import socket
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
        with pytest.raises(ConnectionRefusedError):
            collect(f'http://127.0.0.1:{port}/observations', timeout=0.1)


def test_body_timeout(endpoint):
    url, server, _ = endpoint
    server.extra_length = 0
    server.body_delay = 0.1
    with pytest.raises(TimeoutError):
        collect(url, timeout=0.02)


def test_effect_count_bound():
    from dimaggi_receiver.effect_observation import MAX_EFFECTS
    with pytest.raises(ValueError, match='too_many_effects'):
        parse_snapshot(encoded(*(receipt(str(i)) for i in range(MAX_EFFECTS + 1))))


def test_truncated_http_body(endpoint):
    url, server, _ = endpoint
    server.extra_length = 1
    with pytest.raises(ValueError, match='incomplete_response'):
        collect(url)
