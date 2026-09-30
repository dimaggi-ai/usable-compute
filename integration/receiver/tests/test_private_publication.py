import fcntl
import gc
import os
import sqlite3
import time
import weakref
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read, iso
from test_failure_ownership import Fault


def test_scoped_public_files_survive_startup(published):
    store, path, ledger = published
    foreign = path.parent/(store._temporary_prefix()+'abcdefgh')
    operator = path.parent/(store._temporary_prefix()+'operator')
    operator.write_bytes(b'operator data')
    other = w.WatchStore(foreign, 't', 'c', 'nodes')
    now = time.time()
    other.relist({'apiVersion':'v1', 'kind':'NodeList', 'metadata':{'resourceVersion':'1'},
                  'items':[]}, iso(now), iso(now+290))
    before = foreign.read_bytes(), foreign.stat().st_ino
    store.close()
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        assert (foreign.read_bytes(), foreign.stat().st_ino) == before
        assert operator.read_bytes() == b'operator data'
        assert not read(foreign, ledger)['issues']
    finally:
        replacement.close()
        other.close()


def test_private_temp_rename_stamps_and_syncs_both_directories(published, monkeypatch):
    store, path, ledger = published
    private = Path(store.writer_path).parent
    residue = private/(store._temporary_prefix()+'leftover')
    residue.write_bytes(b'crash residue')
    store._clean_temporary_copies()
    assert not residue.exists()
    replace, sync = os.replace, os.fsync
    moved, synced = [], []
    def rename(source, target):
        if Path(target) == path:
            assert Path(source).parent == private
            initial = os.stat(source).st_ctime_ns
            time.sleep(0.01)
            replace(source, target)
            assert os.stat(target).st_ctime_ns > initial
            moved.append(target)
        else: replace(source, target)
    def fsync(fd):
        synced.append(os.fstat(fd).st_ino)
        sync(fd)
    monkeypatch.setattr(os, 'replace', rename)
    monkeypatch.setattr(os, 'fsync', fsync)
    store.heartbeat()
    assert moved
    assert private.stat().st_ino in synced and path.parent.stat().st_ino in synced
    assert not read(path, ledger)['issues']




def test_startup_probe_allocation_failure_releases_source(tmp_path, monkeypatch):
    gc.collect()
    descriptors = '/proc/self/fd' if Path('/proc/self/fd').exists() else '/dev/fd'
    baseline = len(os.listdir(descriptors))
    original = w.tempfile.mkstemp
    calls = 0
    def allocate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2: raise OSError('probe allocation failed')
        return original(*args, **kwargs)
    monkeypatch.setattr(w.tempfile, 'mkstemp', allocate)
    with pytest.raises(OSError, match='probe allocation failed'):
        w.WatchStore(tmp_path/'db', 't', 'c', 'nodes')
    assert len(os.listdir(descriptors)) == baseline
    assert not list(tmp_path.rglob('.watch-*'))
