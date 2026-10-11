import fcntl
import os
from unittest.mock import patch
import pytest
import rsi_admission as a
from test_rsi_admission import fixture, enc


def setup(tmp_path, monkeypatch):
    db, anchor = tmp_path/'r.db', tmp_path/'anchor.json'
    a.provision_replay_store(db, anchor)
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(anchor))
    m, p, _ = fixture()
    return db, anchor, [(enc(m), enc(p))]


def test_concurrent_consumer_gets_domain_refusal(tmp_path, monkeypatch):
    _, anchor, batches = setup(tmp_path, monkeypatch)
    with open(str(anchor)+'.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(a.Refusal, match='replay_busy'): a.claim_designated(batches)


def test_owner_recovery_preserves_consumptions(tmp_path, monkeypatch):
    db, anchor, batches = setup(tmp_path, monkeypatch)
    original = anchor.read_bytes()
    with patch.object(os, 'replace', side_effect=OSError('injected crash')):
        with pytest.raises(OSError): a.claim_designated(batches)
    with pytest.raises(a.Refusal, match='anchor_mismatch'): a.claim_designated(batches)
    # The pre-existing failed-closed state is the behavioral baseline.
    recover = getattr(a, 'recover_replay_store', lambda *args, **kw: a.claim_designated(batches))
    with pytest.raises(a.Refusal): recover(anchor)
    assert anchor.read_bytes() == original
    report = recover(anchor, confirm=True)
    assert len(report['ahead']) == 4
    assert all(entry['kind'] == 'consume' for entry in report['ahead'])
    with a.ReplayStore(db) as store:
        assert store.db.execute('SELECT count(*) FROM consumed').fetchone()[0] == 4
        assert a._store_head(store)['count'] == 5
    with pytest.raises(a.Refusal, match='cross_call_replay'): a.claim_designated(batches)


def test_recovery_refuses_corrupt_chain(tmp_path, monkeypatch):
    db, anchor, batches = setup(tmp_path, monkeypatch)
    with patch.object(os, 'replace', side_effect=OSError('injected crash')):
        with pytest.raises(OSError): a.claim_designated(batches)
    with a.ReplayStore(db) as store:
        store.db.execute("UPDATE consumed SET identity='corrupt' WHERE rowid=1")
        store.db.commit()
    recover = getattr(a, 'recover_replay_store', lambda *args, **kw: a.claim_designated(batches))
    with pytest.raises(a.Refusal): recover(anchor, confirm=True)


def test_recovery_cli_previews_without_mutating_then_confirms(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from pathlib import Path
    db, anchor, batches = setup(tmp_path, monkeypatch)
    with patch.object(os, 'replace', side_effect=OSError('injected crash')):
        with pytest.raises(OSError): a.claim_designated(batches)
    before = (db.read_bytes(), anchor.read_bytes())
    command = [sys.executable, str(Path(a.__file__)), '--recover-anchor', str(anchor)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert json.loads(result.stdout)['error'] == 'owner_confirmation_required'
    assert len(json.loads(result.stdout)['report']['ahead']) == 4
    assert (db.read_bytes(), anchor.read_bytes()) == before
    result = subprocess.run(command+['--confirm-owner-recovery'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout + result.stderr
    with pytest.raises(a.Refusal, match='cross_call_replay'): a.claim_designated(batches)


def test_recovery_itself_can_crash_and_resume_without_releasing_rows(tmp_path, monkeypatch):
    db, anchor, batches = setup(tmp_path, monkeypatch)
    with patch.object(os, 'replace', side_effect=OSError('injected crash')):
        with pytest.raises(OSError): a.claim_designated(batches)
        with pytest.raises(OSError): a.recover_replay_store(anchor, confirm=True)
    report = a.recover_replay_store(anchor, confirm=True)
    assert [entry['kind'] for entry in report['ahead']] == ['consume']*4+['recovery']
    with pytest.raises(a.Refusal, match='cross_call_replay'): a.claim_designated(batches)


@pytest.mark.parametrize('ahead', [False, True])
def test_legacy_chain_recovers_without_rebasing(tmp_path, monkeypatch, ahead):
    import sqlite3
    db, anchor, batches = setup(tmp_path, monkeypatch)
    original = a.load(anchor.read_bytes())
    # The old schema has only registry and consumed, with the same v1 hash chain.
    with sqlite3.connect(db) as conn: conn.execute('DROP TABLE journal')
    with sqlite3.connect(db) as conn:
        if ahead: conn.execute('INSERT INTO consumed VALUES(?)', ('a'*64,))
    with pytest.raises(a.Refusal, match='owner_confirmation_required'): a.recover_replay_store(anchor)
    report = a.recover_replay_store(anchor, confirm=True)
    assert report['anchor'] == original
    assert len(report['ahead']) == int(ahead)
    with a.ReplayStore(db) as store:
        assert a._store_head(store)['count'] == int(ahead)+1
        assert store.db.execute('SELECT count(*) FROM consumed').fetchone()[0] == int(ahead)
    a.claim_designated(batches)
    with pytest.raises(a.Refusal, match='cross_call_replay'): a.claim_designated(batches)
