"""Independent local-TLS faults; no cluster, real workload or hard DNS deadline."""
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import subprocess
import threading
import time

import pytest

from dimaggi_receiver.kubernetes_collect import Collector
from dimaggi_receiver.observations import ObservationError, ObservationStore
from test_kubernetes_collect import lab, tls_material, collect, NOW


@pytest.fixture
def tls_peer(tls_material):
    certificate, key = tls_material

    @contextmanager
    def factory(reply):
        calls = []
        stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                calls.append((self.command, self.path))
                try:
                    reply(self, stop)
                except (OSError, ssl.SSLError):
                    # The collector deliberately interrupts outstanding socket I/O.
                    pass

            def do_POST(self):
                calls.append((self.command, self.path))
                self.send_error(405)

            do_PUT = do_POST
            do_PATCH = do_POST
            do_DELETE = do_POST

        peer = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        peer.socket = context.wrap_socket(peer.socket, server_side=True)
        worker = threading.Thread(target=peer.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        worker.start()
        try:
            yield f'https://127.0.0.1:{peer.server_port}', calls
        finally:
            stop.set()
            peer.shutdown()
            peer.server_close()
            worker.join(timeout=2)
            assert not worker.is_alive()

    return factory


@pytest.mark.parametrize('stage', ['headers', 'body'])
def test_trickling_tls_peer_cannot_extend_postconnect_collection_deadline(lab, tls_peer, stage):
    """Bytes arrive faster than socket timeout; the total deadline must still win."""
    def trickle(handler, stop):
        if stage == 'headers':
            handler.connection.sendall(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
        else:
            handler.connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nConnection: close\r\n\r\n')
        # Finite independent upper bound prevents a broken implementation hanging CI.
        for _ in range(100):
            if stop.wait(0.02):
                return
            handler.connection.sendall(b'x')
        handler.close_connection = True

    with tls_peer(trickle) as (endpoint, calls):
        config = replace(lab[0], endpoint=endpoint, timeout_seconds=0.08, max_collection_seconds=0.25)
        started = time.monotonic()
        with pytest.raises(ObservationError) as failure:
            collect(lab, config)
        elapsed = time.monotonic() - started
        assert elapsed < 1.25, f'{stage} trickle escaped total deadline: {elapsed}'
        assert calls == [('GET', '/api/v1/namespaces/synthetic-lab')]
        assert lab[3].history('request-1') == []
        assert config.bearer_token not in str(failure.value)


def test_redirect_to_second_tls_origin_is_not_followed_or_written(lab, tls_peer):
    def target(handler, stop):
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(b'{}')

    with tls_peer(target) as (destination, destination_calls):
        def redirect(handler, stop):
            handler.send_response(307)
            handler.send_header('Location', destination + '/credential-trap')
            handler.send_header('Content-Length', '0')
            handler.end_headers()
        with tls_peer(redirect) as (origin, origin_calls):
            with pytest.raises(ObservationError):
                collect(lab, replace(lab[0], endpoint=origin))
            assert origin_calls == [('GET', '/api/v1/namespaces/synthetic-lab')]
            assert destination_calls == []
            assert lab[3].history('request-1') == []


def test_unrelated_ca_never_reaches_http_or_appends(lab, tmp_path):
    cert, key = tmp_path/'unrelated.pem', tmp_path/'unrelated-key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),
                    '-out',str(cert),'-days','1','-subj','/CN=unrelated-trust'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    config = replace(lab[0], ca_pem=cert.read_text())
    with pytest.raises(ObservationError) as failure:
        collect(lab, config)
    assert lab[2] == []
    assert lab[3].history('request-1') == []
    assert config.bearer_token not in str(failure.value)


@pytest.mark.parametrize('resource', ['namespace', 'job', 'pods', 'pod'])
def test_null_metadata_refuses_cleanly_without_append(lab, resource):
    if resource == 'pod':
        lab[1]['pods']['items'][0]['metadata'] = None
    else:
        lab[1][resource]['metadata'] = None
    with pytest.raises(ObservationError):
        collect(lab)
    assert lab[3].history('request-1') == []


def test_actual_store_reopen_preserves_epoch_sequence_and_conflict(lab, tmp_path):
    config, resources, calls, store, kwargs = lab
    collect(lab)
    first = store.history('request-1')
    store.close()
    with ObservationStore(tmp_path/'observations.sqlite', create=False) as reopened:
        restarted = Collector(config, clock=lambda: NOW)
        restarted.collect(reopened, **kwargs)
        events = reopened.history('request-1')
        assert [(x['event']['source_epoch'], x['event']['source_sequence']) for x in events] == [('epoch-1',1),('epoch-1',2)]
        assert events[0] == first[0]
        # Same immutable object version with a different body must not append.
        resources['job']['status']['failed'] = 1
        with pytest.raises(ObservationError):
            restarted.collect(reopened, **kwargs)
        assert len(reopened.history('request-1')) == 2
        assert config.bearer_token not in json.dumps(reopened.history('request-1'))


def test_configuration_epoch_refusal_happens_before_network(lab):
    collect(lab)
    calls_before = list(lab[2])
    rotated = replace(lab[0], endpoint=lab[0].endpoint.replace('127.0.0.1','localhost'))
    with pytest.raises(ObservationError):
        collect(lab, rotated)
    assert lab[2] == calls_before
    assert len(lab[3].history('request-1')) == 1
    collect(lab, replace(rotated, source_epoch='epoch-2'))
    assert [(e['event']['source_epoch'],e['event']['source_sequence']) for e in lab[3].history('request-1')] == [('epoch-1',1),('epoch-2',1)]
