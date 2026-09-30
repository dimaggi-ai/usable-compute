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


@pytest.mark.parametrize('retirement', ['expired', 'commit'])
def test_close_preserves_published_retirement(published, monkeypatch, retirement):
    store, path, ledger = published
    old = path.read_bytes()
    backup = path.parent/'backup'
    with sqlite3.connect(backup) as db: store.db.backup(db)
    with monkeypatch.context() as patch:
        if retirement == 'expired':
            store.last_tick -= w.LEASE_SECONDS + 1
        else:
            patch.setattr(store, 'db', Fault(store.db, lambda sql: sql == 'COMMIT'))
        with pytest.raises((ValueError, sqlite3.Error)): store.heartbeat()
    before = path.read_bytes(), path.stat().st_ino
    with monkeypatch.context() as patch:
        def broken(): raise OSError('publication unavailable')
        patch.setattr(store, '_publish_snapshot', broken)
        store.close()
    assert (path.read_bytes(), path.stat().st_ino) == before
    with pytest.raises(ValueError, match='closed or expired'): read(path, ledger)
    assert len(ledger.read_text().splitlines()) == 2
    Path(store.writer_path).write_bytes(backup.read_bytes())
    restored = path.parent/'restored'
    restored.write_bytes(old)
    os.replace(restored, path)
    with pytest.raises(ValueError, match='previously expired'): read(path, ledger)


