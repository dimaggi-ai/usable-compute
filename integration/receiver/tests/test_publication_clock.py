import os
from types import SimpleNamespace

import pytest
from dimaggi_receiver import topology_watch as w
from test_publication_evidence import published, read


def test_read_samples_ctime_before_sql(published, monkeypatch):
    _, path, ledger = published
    original = os.fstat
    inode = path.stat()
    samples = 0
    def fstat(fd):
        nonlocal samples
        info = original(fd)
        if (info.st_dev, info.st_ino) == (inode.st_dev, inode.st_ino):
            samples += 1
            fields = {k: getattr(info, k) for k in dir(info) if k.startswith('st_')}
            # Each read's first sample witnesses the timely rename. A later
            # sample models metadata changed after SQL, without changing bytes.
            if samples % 2 == 0: fields['st_ctime'] += 11
            return SimpleNamespace(**fields)
        return info
    monkeypatch.setattr(os, 'fstat', fstat)
    # Check one read at a time so the next read does not inherit the bump.
    _, _, _, lease, _, stamp = w._read_rows(path)
    assert stamp-lease[1] <= w.COMMIT_BOUND_SECONDS
    assert samples == 1


@pytest.mark.parametrize('offset', [-2.001, -3, -2, -1])
def test_publication_clock_lower_bound_without_tombstone(published, monkeypatch, offset):
    store, path, ledger = published
    h = store.db.execute('SELECT heartbeat FROM lease').fetchone()[0]
    inode, original = path.stat(), os.fstat
    def fstat(fd):
        info = original(fd)
        if (info.st_dev, info.st_ino) != (inode.st_dev, inode.st_ino): return info
        fields = {k: getattr(info, k) for k in dir(info) if k.startswith('st_')}
        return SimpleNamespace(**dict(fields, st_ctime=h+offset))
    monkeypatch.setattr(os, 'fstat', fstat)
    before = ledger.read_bytes()
    if offset < -2:
        with pytest.raises(ValueError, match='publication'): read(path, ledger)
    else:
        assert not read(path, ledger)['issues']
    assert ledger.read_bytes() == before


@pytest.mark.parametrize('advances', [False, True])
def test_acquisition_checks_rename_ctime(tmp_path, monkeypatch, advances):
    original = os.stat
    def stat(p, *args, **kwargs):
        info = original(p, *args, **kwargs)
        if not advances and '.watch-' in str(p):
            fields = {k: getattr(info, k) for k in dir(info) if k.startswith('st_')}
            return SimpleNamespace(**dict(fields, st_ctime=1, st_ctime_ns=1000000000))
        return info
    monkeypatch.setattr(os, 'stat', stat)
    path = tmp_path/'db'
    if advances:
        store = w.WatchStore(path, 't', 'c', 'nodes')
        store.close()
    else:
        with pytest.raises(ValueError, match='rename.*ctime'):
            w.WatchStore(path, 't', 'c', 'nodes')
        assert not path.exists()
    assert not list(tmp_path.glob('.watch-*'))


def test_chmod_during_sql_keeps_pre_read_witness(published, monkeypatch):
    import sqlite3
    import time
    _, path, _ = published
    original = sqlite3.connect
    class MetadataChange:
        def __init__(self, db): self.db = db
        def __getattr__(self, name): return getattr(self.db, name)
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if sql == 'COMMIT':
                time.sleep(0.08)
                path.chmod(0o440)
            return result
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        return MetadataChange(db) if kwargs.get("uri") else db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    _, _, _, lease, _, stamp = w._read_rows(path)
    assert stamp-lease[1] < 0.05
    assert path.stat().st_ctime-lease[1] > 0.05
