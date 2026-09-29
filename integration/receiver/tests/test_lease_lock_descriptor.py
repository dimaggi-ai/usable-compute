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
''', str(store.writer_path)], capture_output=True, text=True, timeout=3, check=True)
        assert result.stdout.strip() == 'blocked'
    finally:
        store.db.execute('ROLLBACK')


def test_missing_reader_sidecar_is_not_required(state):
    store, path, ledger, start = state
    sidecar = path.with_name(path.name+'.lease-lock')
    assert not sidecar.exists()
    before = ledger.read_bytes()
    assert not read(path, ledger)['issues']
    assert ledger.read_bytes() == before
