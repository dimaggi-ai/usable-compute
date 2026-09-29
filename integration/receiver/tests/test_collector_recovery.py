"""Fake-transport collection tests; no live Kubernetes qualification."""
from datetime import datetime, timedelta, timezone
import json
import pytest
from dimaggi_receiver import topology_collect as c, topology_watch as w
from test_topology_watch import listing, T


@pytest.mark.parametrize('mode', ['quiet', 'gone', 'error_frame'])
def test_fake_transport_collector_keeps_running(tmp_path, monkeypatch, mode):
    start = datetime.fromisoformat(T.replace('Z', '+00:00'))
    seconds = [0]; calls = []
    clock = lambda: start + timedelta(seconds=seconds[0])
    monkeypatch.setattr(w.time, 'time', lambda: clock().timestamp())
    config = dict(endpoint='https://cluster.invalid', ca_pem='test', bearer_token='test', timeout_seconds=30, response_limit=100000)
    def fetch(config, path):
        calls.append(path)
        if 'watch=true' not in path:
            payload = listing(); payload['metadata']['resourceVersion'] = str(len(calls))
            return dict(status=200, body=json.dumps(payload))
        if mode == 'gone': return dict(status=410, body='')
        if mode == 'error_frame': return dict(status=200, body=json.dumps({'type':'ERROR','object':{'code':410}}))
        return dict(status=200, body='')
    monkeypatch.setattr(c, 'fetch', fetch)
    s = w.WatchStore(tmp_path/'w.db', 't', 'c', 'nodes')
    try:
        for tick in range(0, 661, 20):
            seconds[0] = tick
            expiry = (clock()+timedelta(seconds=300)).isoformat().replace('+00:00','Z')
            snapshot = c.collect(s, config, expires_at=expiry, watch=tick>0, clock=clock)
            assert not snapshot['issues']
            assert not w.read_current(tmp_path/'w.db', tenant='t', cluster='c', collection='nodes', now=clock().isoformat().replace('+00:00','Z'))['issues']
        assert sum('watch=true' not in path for path in calls) >= 3
    finally: s.close()
