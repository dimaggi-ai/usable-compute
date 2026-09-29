import shutil
import sqlite3
from pathlib import Path

import pytest
from dimaggi_receiver import topology_watch as w
from test_reader_ledger import state, read
from test_topology_watch import T, E, listing


def test_legacy_database_import_requires_new_generation_and_relist(state):
    store, path, ledger, start = state
    identity = store.db.execute('SELECT identity FROM store_identity').fetchone()
    generation = store.generation
    store.close()
    shutil.rmtree(Path(store.writer_path).parent)
    with sqlite3.connect(path) as legacy:
        legacy.execute('DROP TABLE publication_protocol')
    before = ledger.read_bytes()
    with pytest.raises(ValueError): read(path, ledger)
    assert ledger.read_bytes() == before
    replacement = w.WatchStore(path, 't', 'c', 'nodes')
    try:
        assert replacement.generation != generation
        assert replacement.db.execute('SELECT identity FROM store_identity').fetchone() == identity
        with pytest.raises(ValueError): read(path, ledger)
        payload = listing()
        payload['metadata']['resourceVersion'] = '12'
        replacement.relist(payload, T, E)
        assert not read(path, ledger)['issues']
    finally:
        replacement.close()


def test_publication_retains_group_and_mode_on_a_new_inode(state):
    store, path, ledger, start = state
    path.chmod(0o640)
    before = path.stat()
    store.heartbeat()
    after = path.stat()
    assert before.st_ino != after.st_ino
    assert (after.st_gid, after.st_mode & 0o777) == (before.st_gid, 0o640)
    assert Path(store.writer_path).parent.stat().st_mode & 0o777 == 0o700
    assert store.db.execute('PRAGMA journal_mode').fetchone() == ('wal',)
    with sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True) as snapshot:
        assert snapshot.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    assert not read(path, ledger)['issues']
