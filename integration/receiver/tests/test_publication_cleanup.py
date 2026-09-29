import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read


def test_startup_removes_obsolete_lock_and_journals(published):
    store, path, ledger = published
    store.close()
    shutil.rmtree(Path(store.writer_path).parent)
    Path(str(path)+'.lease-lock').touch()
    # A cold DELETE legacy database with empty journal residue, plus the separate
    # killed-WAL migration probes, exercises all three cleanup names.
    for suffix in ('-wal', '-shm', '-journal'):
        Path(str(path)+suffix).touch()
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        assert not Path(str(path)+'.lease-lock').exists()
        assert all(not Path(str(path)+x).exists() for x in ('-wal', '-shm', '-journal'))
    finally:
        replacement.close()


def test_restart_cleans_only_owned_regular_inactive_temps(published):
    import fcntl
    store, path, ledger = published
    store.close()
    residue = path.parent/'.watch-crashed1'
    residue.write_bytes(b'x'*65536)
    outside = path.parent/'keep'; outside.write_text('untouched')
    link = path.parent/'.watch-symlink1'; link.symlink_to(outside)
    active = path.parent/'.watch-active01'; active.touch()
    with active.open('rb') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        replacement = w.WatchStore(path, 't', 'c', 'nodes')
        replacement.close()
        assert active.exists()
    assert not residue.exists()
    assert link.is_symlink() and outside.read_text() == 'untouched'


def test_copy_stays_private_until_data_fsync(published, monkeypatch):
    store, path, ledger = published
    path.chmod(0o640)
    original = os.fsync
    modes = []
    def sync(fd):
        info = os.fstat(fd)
        if stat.S_ISREG(info.st_mode): modes.append(stat.S_IMODE(info.st_mode))
        return original(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    store.heartbeat()
    assert modes == [0o600]
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_publication_removes_reintroduced_journal_residue(published):
    store, path, ledger = published
    for suffix in ('-wal', '-shm', '-journal'):
        Path(str(path)+suffix).write_bytes(b'obsolete')
    store.heartbeat()
    assert all(not Path(str(path)+x).exists() for x in ('-wal', '-shm', '-journal'))
    assert not read(path, ledger)['issues']


def test_cleanup_preserves_other_stores_and_operator_files(published):
    store, path, ledger = published
    foreign = path.parent/'.watch-east.db'
    other = w.WatchStore(foreign, 't', 'c', 'nodes')
    try:
        from test_publication_evidence import iso
        import time
        now = time.time()
        other.relist({'apiVersion': 'v1', 'kind': 'NodeList',
                      'metadata': {'resourceVersion': '1'}, 'items': []}, iso(now), iso(now+290))
        keep = [path.parent/'.watch-notes.txt', path.parent/(other._temporary_prefix()+'abcdefgh'),
                path.parent/'.watch-abcdefghi', path.parent/'.watch-ABCDEFGH']
        remove = [path.parent/(store._temporary_prefix()+'abcdefgh'), path.parent/'.watch-ab12_cd3']
        for item in keep + remove: item.write_text('retain or clean')
        store._clean_temporary_copies()
        assert foreign.exists()
        assert not read(foreign, ledger)['issues']
        assert all(item.exists() for item in keep)
        assert all(not item.exists() for item in remove)
    finally:
        other.close()
