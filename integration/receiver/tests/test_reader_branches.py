import fcntl
import os
import sqlite3
import time
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read


def test_second_read_refuses_late_newer_heartbeat(published, monkeypatch):
    store, path, ledger = published
    first_h = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    original = w._read_rows
    calls = 0
    monkeypatch.setattr(w, 'COMMIT_BOUND_SECONDS', 0.05)
    def rows(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            newer = time.time()
            assert newer > first_h
            store.db.execute('UPDATE lease SET heartbeat=?', (newer,))
            time.sleep(0.08)
            store._publish()
        return original(*args, **kwargs)
    monkeypatch.setattr(w, '_read_rows', rows)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='publication exceeded'): read(path, ledger)
    assert ledger.read_bytes() == before


def test_canonical_open_replacement_checks_descriptor_identity(published, monkeypatch):
    store, path, ledger = published
    connect = sqlite3.connect
    fired = False
    def canonical(database, *args, **kwargs):
        uri = database
        nonlocal fired
        if isinstance(uri, str) and uri.startswith('file:/proc/self/fd/') and not fired:
            fired = True
            # Model canonicalization completing before replacement, followed by
            # the database open using that already resolved pathname.
            resolved = os.readlink(uri[5:].split('?')[0])
            store.heartbeat()
            uri = 'file:' + resolved + '?mode=ro&immutable=1'
        return connect(uri, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', canonical)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='watch changed'): read(path, ledger)
    assert fired and ledger.read_bytes() == before


@pytest.mark.parametrize('cause', ['closed', 'aged'])
def test_second_read_records_permanent_death(published, monkeypatch, cause):
    store, path, ledger = published
    original = w._read_rows
    calls = 0
    h = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    def rows(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            if cause == 'closed': store.close()
            else: monkeypatch.setattr(w.time, 'time', lambda: h+57)
        return original(*args, **kwargs)
    monkeypatch.setattr(w, '_read_rows', rows)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
    assert ledger.read_bytes() != before


def test_reader_refuses_without_kernel_platform(published, monkeypatch):
    _, path, ledger = published
    monkeypatch.setattr(w.sys, 'platform', 'unsupported')
    with pytest.raises(ValueError, match='kernel publication'): read(path, ledger)


def test_nofollow_refuses_transient_symlink(published, monkeypatch):
    _, path, ledger = published
    alias = path.with_name('alias')
    os.link(path, alias)
    original = os.open
    fired = False
    def open_link(p, *args, **kwargs):
        nonlocal fired
        if str(p) != str(path) or fired: return original(p, *args, **kwargs)
        fired = True
        path.unlink()
        path.symlink_to(alias)
        try:
            return original(p, *args, **kwargs)
        finally:
            path.unlink()
            os.link(alias, path)
    monkeypatch.setattr(os, 'open', open_link)
    before = ledger.read_bytes()
    with pytest.raises(ValueError, match='unavailable'): read(path, ledger)
    assert fired and ledger.read_bytes() == before


def test_publisher_holds_exclusive_temp_lock(published, monkeypatch):
    store, path, _ = published
    original = os.fsync
    checked = []
    def sync(fd):
        name = Path(os.readlink('/proc/self/fd/'+str(fd)))
        if name.name.startswith(store._temporary_prefix()):
            with name.open('rb') as other:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checked.append(name)
        return original(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    store.heartbeat()
    assert len(checked) == 1
