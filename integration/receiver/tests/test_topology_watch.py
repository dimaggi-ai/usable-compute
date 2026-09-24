"""Durable watch and real local TLS conformance; no hardware qualification."""
import copy
from datetime import datetime
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from dimaggi_receiver.topology_watch import WatchStore
from dimaggi_receiver.topology_collect import collect
from dimaggi_receiver.observations import ObservationError
from test_kubernetes_collect import tls_material

T='2026-09-24T00:00:00Z'; E='2026-09-24T00:01:00Z'
def node(rv='a', uid='uid1'):
    return {'metadata':{'name':'n1','uid':uid,'resourceVersion':rv}}
def listing():
    return {'apiVersion':'v1','kind':'NodeList','metadata':{'resourceVersion':'list-a'},'items':[node()]}
def event(typ='MODIFIED', rv='b', uid='uid1'):
    return {'type':typ,'object':node(rv,uid)}

@pytest.fixture
def store(tmp_path):
    s=WatchStore(tmp_path/'watch.db','tenant','cluster','nodes')
    s.relist(listing(),T,E)
    yield s
    s.close()

def test_ordered_changes_duplicates_deletion_reuse(store):
    store.apply([event()],T,E)
    store.apply([event()],T,E)
    assert store.snapshot(T)['events']==1
    store.apply([event('DELETED','c'),event('ADDED','d','uid2')],T,E)
    assert store.snapshot(T)['records']['n1']['metadata']['uid']=='uid2'

@pytest.mark.parametrize('events',[[event(uid='other')],[event('ERROR')],[event('ADDED')],[event(rv='list-a')],[event()]*1025])
def test_fault_requires_durable_relist(store,events):
    with pytest.raises(ObservationError):store.apply(events,T,E)
    assert store.snapshot(T)['issues']==['resync_required']
    with pytest.raises(ObservationError):store.apply([event()],T,E)
    store.relist(listing(),T,E)
    assert not store.snapshot(T)['issues']

def test_atomic_batch_and_restart(tmp_path):
    path=tmp_path/'watch.db';s=WatchStore(path,'t','c','nodes');s.relist(listing(),T,E)
    with pytest.raises(ObservationError):s.apply([event(),event(uid='other',rv='c')],T,E)
    assert s.snapshot(T)['records']['n1']['metadata']['resourceVersion']=='a'
    s.relist(listing(),T,E);s.close()
    s=WatchStore(path,'t','c','nodes')
    assert 'resync_required' in s.snapshot(T)['issues']
    s.close()

def test_new_owner_invalidates_old_stream(store):
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    other=WatchStore(path,'tenant','cluster','nodes')
    try:
        other.relist(listing(),T,E)
        with pytest.raises(ObservationError):store.apply([event()],T,E)
        assert 'resync_required' in other.snapshot(T)['issues']
    finally:other.close()

def test_partial_list_scope_and_staleness(store):
    bad=listing();bad['metadata']['continue']='more'
    with pytest.raises(ObservationError):store.relist(bad,T,E)
    assert store.snapshot(E)['issues']==['stale_or_future']
    bad=listing();bad['items'][0]['metadata']['namespace']='foreign'
    with pytest.raises(ObservationError):store.relist(bad,T,E)

def test_real_tls_list_watch_and_gone(store,tls_material):
    cert,key=tls_material; calls=[]; state={'status':200}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            calls.append(self.path)
            assert self.headers['Authorization']=='Bearer TEST-TOPOLOGY-TOKEN'
            raw=(json.dumps(event())+'\n').encode() if 'watch=true' in self.path else json.dumps(listing()).encode()
            self.send_response(state['status']);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    peer=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    peer.socket=tls.wrap_socket(peer.socket,server_side=True)
    thread=threading.Thread(target=peer.serve_forever,daemon=True);thread.start()
    config=dict(endpoint=f'https://127.0.0.1:{peer.server_port}',ca_pem=cert.read_text(),bearer_token='TEST-TOPOLOGY-TOKEN',timeout_seconds=5,response_limit=100000)
    try:
        collect(store,config,clock=lambda:datetime.fromisoformat(T.replace("Z","+00:00")),expires_at=E)
        collect(store,config,clock=lambda:datetime.fromisoformat(T.replace("Z","+00:00")),expires_at=E,watch=True)
        assert store.snapshot(T)['resource_version']=='b'
        assert 'resourceVersion=list-a' in calls[1]
        state['status']=410
        with pytest.raises(ObservationError):collect(store,config,clock=lambda:datetime.fromisoformat(T.replace("Z","+00:00")),expires_at=E,watch=True)
        assert store.snapshot(T)['issues']==['resync_required']
        state['status']=302
        with pytest.raises(ObservationError):collect(store,config,clock=lambda:datetime.fromisoformat(T.replace("Z","+00:00")),expires_at=E)
        assert 'TEST-TOPOLOGY-TOKEN' not in json.dumps(store.snapshot(T))
    finally:peer.shutdown();peer.server_close();thread.join()

def test_process_death_during_transaction_preserves_committed_rows(tmp_path):
    import subprocess
    import sys
    import time
    path=tmp_path/'watch.db';marker=tmp_path/'transaction-started'
    s=WatchStore(path,'t','c','nodes');s.relist(listing(),T,E);s.close()
    code='''import sqlite3,sys,time
from pathlib import Path
s=sqlite3.connect(sys.argv[1],isolation_level=None)
s.execute('BEGIN IMMEDIATE')
s.execute("UPDATE projection SET body='uncommitted-corruption'")
Path(sys.argv[2]).write_text('ready')
time.sleep(60)
'''
    proc=subprocess.Popen([sys.executable,'-c',code,str(path),str(marker)])
    try:
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert marker.exists();proc.kill();proc.wait(timeout=5)
    finally:
        if proc.poll() is None:proc.kill();proc.wait()
    s=WatchStore(path,'t','c','nodes')
    assert s.snapshot(T)['records']['n1']['metadata']['uid']=='uid1'
    assert s.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert s.snapshot(T)['issues']==['resync_required'];s.close()
