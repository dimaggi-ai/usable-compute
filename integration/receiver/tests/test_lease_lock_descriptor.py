import subprocess
import sys

from test_reader_ledger import state, read


def test_reader_preserves_other_connection_sqlite_reservation(state):
    store, path, ledger, start = state
    store.db.execute('BEGIN IMMEDIATE')
    try:
        assert not read(path, ledger)['issues']
        result = subprocess.run([sys.executable, '-c', '''
import sqlite3, sys
with sqlite3.connect(sys.argv[1], timeout=0.1) as db:
    try:
        db.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError:
        print('blocked')
    else:
        print('acquired')
        db.rollback()
''', str(path)], capture_output=True, text=True, timeout=3, check=True)
        assert result.stdout.strip() == 'blocked'
    finally:
        store.db.execute('ROLLBACK')


def test_missing_sidecar_refuses_without_recording_expiry(state):
    import pytest
    store, path, ledger, start = state
    sidecar = path.with_name(path.name+'.lease-lock')
    retained = sidecar.with_suffix('.retained')
    before = ledger.read_bytes()
    sidecar.rename(retained)
    try:
        with pytest.raises(ValueError, match='current topology unavailable'):
            read(path, ledger)
        assert ledger.read_bytes() == before
    finally:
        retained.rename(sidecar)
