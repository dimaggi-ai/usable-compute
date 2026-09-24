"""One bounded TLS Kubernetes list/watch cycle, with no ambient credentials.

The configured API origin and CA are the trust boundary. A subprocess enforces a
hard deadline including DNS. Only GET paths assembled here are accepted. Watch
responses are buffered within a fixed bound, then applied atomically. Overflow
invalidates the projection. No mutation, retry or automatic version negotiation.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlencode
from .jsonio import loads
from .topology import need
from .topology_watch import KINDS


def fetch(config, path):
    allowed = {'endpoint','ca_pem','bearer_token','timeout_seconds','response_limit'}
    need(type(config) is dict and set(config) == allowed, 'exact TLS configuration required')
    endpoint = urlsplit(config['endpoint'])
    need(endpoint.scheme == 'https' and endpoint.hostname and endpoint.path in ('','/')
         and not endpoint.username and not endpoint.password and not endpoint.query and not endpoint.fragment,
         'TLS origin required')
    need(type(config['timeout_seconds']) is int and 1 <= config['timeout_seconds'] <= 30, 'deadline bound refused')
    need(type(config['response_limit']) is int and 1 <= config['response_limit'] <= 8*1024*1024, 'response bound refused')
    need(type(config['bearer_token']) is str and 0 < len(config['bearer_token']) <= 8192
         and all(32 < ord(c) < 127 for c in config['bearer_token']), 'invalid credential')
    need(type(config['ca_pem']) is str and 0 < len(config['ca_pem']) <= 1024*1024, 'CA required')
    need(path.startswith(('/api/v1/','/apis/resource.k8s.io/v1/')) and '\n' not in path and '\r' not in path, 'read path refused')
    try:
        result = subprocess.run([sys.executable, '-m', __name__, '--worker'],
            input=json.dumps({'config':config,'path':path}).encode(), capture_output=True,
            timeout=config['timeout_seconds']+1, check=False)
        need(result.returncode == 0, 'TLS collection failed')
        value = loads(result.stdout.decode())
        need(type(value) is dict and value.get('status') in (200,410), 'read status refused')
        return value
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise ValueError('TLS collection failed or deadline exceeded') from None


def collect(store, config, *, expires_at, watch=False, clock=lambda: datetime.now(timezone.utc)):
    observed_at=clock().isoformat().replace("+00:00","Z")
    collection, namespace = store.scope[2:]
    from urllib.parse import quote
    path = '/api/v1/nodes' if collection == 'nodes' else '/apis/resource.k8s.io/v1/'
    if collection != 'nodes':
        if namespace: path += 'namespaces/'+quote(namespace, safe='')+'/'
        path += collection
    try:
        from .observations import _digest
        descriptor=_digest({k:v for k,v in config.items() if k!='bearer_token'})
        if watch:
            prior = store.snapshot(observed_at)
            need(prior['transport_digest']==descriptor, 'TLS source configuration changed; relist required')
            need(not prior['issues'], 'fresh relist required before watch')
            path += '?'+urlencode({'watch':'true','allowWatchBookmarks':'true',
                'resourceVersion':prior['resource_version'],'timeoutSeconds':max(1, config['timeout_seconds']-2)})
        else:
            path += '?limit=10000'
        response = fetch(config, path)
        need(response['status'] == 200, 'resource version expired: relist required')
        from .observations import _utc
        finished=clock().isoformat().replace('+00:00','Z')
        need(_utc(observed_at)<=_utc(finished)<_utc(expires_at), 'collection expired or clock regressed')
        body = response['body']
        if watch:
            lines = body.splitlines()
            need(len(lines) <= 1024 and all(line.strip() for line in lines), 'watch frame bound refused')
            store.apply([loads(line) for line in lines], observed_at, expires_at)
        else:
            store.transport_digest=descriptor
            store.relist(loads(body), observed_at, expires_at)
        return store.snapshot(finished)
    except BaseException:
        store.fail()
        raise


def _worker():
    import http.client
    import ssl
    value = json.loads(sys.stdin.buffer.read(2*1024*1024))
    config, path = value['config'], value['path']
    endpoint = urlsplit(config['endpoint'])
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_verify_locations(cadata=config['ca_pem'])
    conn = http.client.HTTPSConnection(endpoint.hostname, endpoint.port or 443,
        context=tls, timeout=config['timeout_seconds'])
    try:
        conn.request('GET',path,headers={'Authorization':'Bearer '+config['bearer_token'], 'Accept':'application/json','Connection':'close'})
        response = conn.getresponse()
        need(response.status in (200,410) and response.getheader('Content-Encoding') in (None,'identity'), 'read refused')
        body = response.read(config['response_limit']+1)
        need(len(body) <= config['response_limit'] and config['bearer_token'].encode() not in body, 'read limit or credential echo')
        print(json.dumps({'status':response.status,'body':body.decode('utf-8')}))
    finally: conn.close()


if __name__ == '__main__':
    try: _worker()
    except Exception: sys.exit(2)
