import concurrent.futures
import json
import os
import sqlite3
from unittest.mock import patch

import pytest
from dimaggi_receiver import topology_watch as w
from test_lease_fencing import live
from test_topology_watch import T


@pytest.fixture
def state(tmp_path):
    start = w._utc(T).timestamp()
    with patch.object(w.time, 'time', return_value=start):
        store, payload = live(tmp_path/'watch.db')
        ledger = tmp_path/'reader.ledger'
        ledger.write_text('"dimaggi-expiry-ledger/v1"\n')
        ledger.chmod(0o600)
        yield store, tmp_path/'watch.db', ledger, start
        store.close()


def read(path, ledger, now=T, **scope):
    args = dict(tenant='t', cluster='c', collection='nodes', now=now)
    args.update(scope)
    args['expiry_ledger'] = ledger
    return w.read_current(path, **args)


def test_reader_works_without_database_write_permission(state):
    store, path, ledger, start = state
    path.chmod(0o444)
    try:
        assert not os.stat(path).st_mode & 0o222
        with pytest.raises(sqlite3.OperationalError):
            with sqlite3.connect('file:' + str(path) + '?mode=rw', uri=True) as db:
                db.execute('UPDATE lease SET live=1')
        assert read(path, ledger)['issues'] == []
    finally:
        path.chmod(0o600)


def test_reader_never_requests_database_write_reservation(state, monkeypatch):
    store, path, ledger, start = state
    original = sqlite3.connect
    statements = []
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    read(path, ledger)
    assert not any('UPDATE' in s or 'IMMEDIATE' in s for s in statements)


def test_wrong_scope_expiry_changes_neither_store_nor_ledger(state):
    store, path, ledger, start = state
    before = path.read_bytes(), ledger.read_bytes()
    with patch.object(w.time, 'time', return_value=start+57):
        with pytest.raises(ValueError, match='scope'):
            read(path, ledger, tenant='wrong')
    assert (path.read_bytes(), ledger.read_bytes()) == before


def test_two_readers_succeed_while_collector_has_reserved_lock(state):
    store, path, ledger, start = state
    store.db.execute('BEGIN IMMEDIATE')
    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(read, path, ledger) for _ in range(2)]
            assert all(job.result(timeout=8)['issues'] == [] for job in jobs)
    finally:
        store.db.execute('ROLLBACK')


@pytest.mark.parametrize('bad', ['missing', 'mode', 'readonly', 'symlink', 'corrupt', 'truncated', 'owner'])
def test_unusable_ledger_refuses(state, bad, monkeypatch):
    store, path, ledger, start = state
    if bad == 'missing': ledger.unlink()
    if bad == 'mode': ledger.chmod(0o640)
    if bad == 'readonly': ledger.chmod(0o400)
    if bad == 'symlink':
        other = ledger.with_suffix('.real'); ledger.rename(other); ledger.symlink_to(other)
    if bad == 'corrupt': ledger.write_text('{}\n')
    if bad == 'truncated': ledger.write_text('"dimaggi-expiry-ledger/v1"\n[')
    if bad == 'owner': monkeypatch.setattr(os, 'getuid', lambda: os.stat(ledger).st_uid + 1)
    with pytest.raises(ValueError): read(path, ledger)


def test_expired_generation_is_reader_local_and_never_revives(state):
    store, path, ledger, start = state
    before = path.read_bytes()
    with patch.object(w.time, 'time', return_value=start+57):
        with pytest.raises(ValueError): read(path, ledger)
    assert path.read_bytes() == before
    with pytest.raises(ValueError): read(path, ledger)
    # Even a collector-identity rewrite of this generation cannot erase the tombstone.
    store.db.execute('UPDATE lease SET heartbeat=?,live=1', (start,))
    store._publish()
    with pytest.raises(ValueError): read(path, ledger)


def test_ledger_validation_uses_open_descriptor(state, monkeypatch):
    store, path, ledger, start = state
    other = ledger.with_suffix('.other')
    other.write_text('{}\n'); other.chmod(0o644)
    original = os.open
    def swap(p, flags, *args, **kwargs):
        fd = original(p, flags, *args, **kwargs)
        if str(p) == str(ledger):
            ledger.rename(ledger.with_suffix('.retained')); ledger.symlink_to(other)
        return fd
    monkeypatch.setattr(os, 'open', swap)
    assert read(path, ledger)['issues'] == []
    assert other.read_text() == '{}\n'


def test_ledger_keys_include_store_scope_and_generation(state, tmp_path):
    store, path, ledger, start = state
    with patch.object(w.time, 'time', return_value=start+57):
        with pytest.raises(ValueError): read(path, ledger)
    replacement, _ = live(path)
    try:
        assert read(path, ledger)['issues'] == []
        second, _ = live(tmp_path/'second.db')
        try:
            assert read(tmp_path/'second.db', ledger)['issues'] == []
        finally:
            second.close()
    finally:
        replacement.close()


def test_existing_ledger_cannot_be_reinitialized(state):
    store, path, ledger, start = state
    before = ledger.read_bytes()
    with pytest.raises(FileExistsError): w.initialize_expiry_ledger(ledger)
    assert ledger.read_bytes() == before
