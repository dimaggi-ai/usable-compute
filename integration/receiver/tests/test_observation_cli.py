"""Local command workflow and preservation of unconfigured evidence files."""
import json
import sqlite3
import subprocess
import sys

import pytest

from dimaggi_receiver.cli import main


T0 = "2026-09-19T12:00:00Z"


def intent():
    return {"request_id": "request", "report_id": "report", "profile_id": "profile",
            "target_id": "synthetic-target", "workload_id": "job", "desired_state": "succeeded",
            "sources": {role: "synthetic-" + role for role in ("permission", "attempt", "workload")},
            "evidence_class": "synthetic"}


def invoke(capsys, *args, code=0):
    assert main(list(map(str, args))) == code
    return json.loads(capsys.readouterr().out)


def files(tmp_path):
    config = tmp_path / "intent.json"
    config.write_text(json.dumps(intent()))
    ttl = tmp_path / "ttl.json"
    ttl.write_text(json.dumps({role: 60 for role in ("permission", "attempt", "workload")}))
    return config, ttl


def test_init_projection_reconciliation_and_idempotent_triage(tmp_path, capsys):
    config, ttl = files(tmp_path)
    journal = tmp_path / "application.sqlite"
    result = invoke(capsys, "observations-init", "--journal", journal, "--intent", config)
    assert result["execution_authorized"] is False
    args = ("--journal", journal, "--request-id", "request", "--as-of", T0, "--freshness", ttl)
    projected = invoke(capsys, "observations-show", *args)
    assert {case["reason"] for case in projected["cases"]} == {"permission_missing", "attempt_missing", "workload_missing"}
    before = journal.read_bytes()
    assert invoke(capsys, "observations-show", *args) == projected
    assert journal.read_bytes() == before
    reconciled = invoke(capsys, "observations-reconcile", *args)
    note_args = ("observations-triage", "--journal", journal, "--triage-id", "triage-1",
                 "--reconciliation-id", reconciled["reconciliation_id"], "--case-id", reconciled["cases"][0]["case_id"],
                 "--actor-id", "synthetic-reviewer", "--disposition", "hold", "--reason", "source missing", "--recorded-at", T0)
    assert invoke(capsys, *note_args)["appended"] is True
    duplicate = invoke(capsys, *note_args)
    assert duplicate["appended"] is False and len(duplicate["history"]) == 1
    shown = invoke(capsys, "observations-show", *args)
    assert shown["mutation_request"] is None and shown["recommendation"] == "hold"
    assert invoke(capsys, "observations-history", "--journal", journal, "--request-id", "request")["events"] == []


@pytest.mark.parametrize("bad", [None, [], {"sources": []}, {"sources": None},
                                  {**intent(), "evidence_class": "invented"}])
def test_invalid_init_never_creates_file(tmp_path, capsys, bad):
    config = tmp_path / "bad.json"; config.write_text(json.dumps(bad))
    journal = tmp_path / "not-created.sqlite"
    error = invoke(capsys, "observations-init", "--journal", journal, "--intent", config, code=2)
    assert not journal.exists()
    assert error["execution_authorized"] is False


def test_init_preserves_existing_file(tmp_path, capsys):
    config, _ = files(tmp_path)
    journal = tmp_path / "existing.sqlite"; journal.write_bytes(b"user evidence\n")
    invoke(capsys, "observations-init", "--journal", journal, "--intent", config, code=2)
    assert journal.read_bytes() == b"user evidence\n"


@pytest.mark.parametrize("kind", ["missing", "empty", "unrelated", "corrupt", "unsupported_schema"])
def test_show_never_initializes_or_repairs_evidence_files(tmp_path, capsys, kind):
    config, ttl = files(tmp_path); journal = tmp_path / "evidence.sqlite"
    if kind == "empty": journal.touch()
    if kind == "corrupt": journal.write_bytes(b"not SQLite")
    if kind == "unrelated":
        with sqlite3.connect(journal) as db: db.execute("CREATE TABLE user_work(value TEXT)")
    if kind == "unsupported_schema":
        invoke(capsys, "observations-init", "--journal", journal, "--intent", config)
        with sqlite3.connect(journal) as db: db.execute("DROP TRIGGER events_no_update")
    before = journal.read_bytes() if journal.exists() else None
    invoke(capsys, "observations-show", "--journal", journal, "--request-id", "request",
           "--as-of", T0, "--freshness", ttl, code=2)
    assert (journal.read_bytes() if journal.exists() else None) == before


def test_unknown_request_and_invalid_freshness_preserve_configured_file(tmp_path, capsys):
    config, ttl = files(tmp_path); journal = tmp_path / "configured.sqlite"
    invoke(capsys, "observations-init", "--journal", journal, "--intent", config)
    before = journal.read_bytes()
    for request in ("unknown", "request"):
        if request == "request": ttl.write_text('{"permission":true,"attempt":60,"workload":60}')
        invoke(capsys, "observations-show", "--journal", journal, "--request-id", request,
               "--as-of", T0, "--freshness", ttl, code=2)
        assert journal.read_bytes() == before


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_existing_only_access_preserves_recovery_sidecars(tmp_path, capsys, suffix):
    config, ttl = files(tmp_path); journal = tmp_path / "configured.sqlite"
    invoke(capsys, "observations-init", "--journal", journal, "--intent", config)
    sidecar = tmp_path / (journal.name + suffix)
    sidecar.write_bytes(b"retained recovery evidence")
    before = {path.name: path.read_bytes() for path in (journal, sidecar)}
    error = invoke(capsys, "observations-show", "--journal", journal, "--request-id", "request",
                   "--as-of", T0, "--freshness", ttl, code=2)
    assert "recovery sidecars" in error["message"]
    assert before == {path.name: path.read_bytes() for path in (journal, sidecar)}


@pytest.mark.parametrize("configured", [False, True])
def test_crashed_sqlite_writer_is_not_recovered_by_read_cli(tmp_path, capsys, configured):
    config, ttl = files(tmp_path); journal = tmp_path / "evidence.sqlite"
    if configured:
        invoke(capsys, "observations-init", "--journal", journal, "--intent", config)
    else:
        with sqlite3.connect(journal) as db:
            db.execute("CREATE TABLE user_evidence(id INTEGER PRIMARY KEY, payload TEXT)")
            db.executemany("INSERT INTO user_evidence VALUES (?,?)", [(i, "a" * 4000) for i in range(300)])
    # A real child exits after pages spill from an uncommitted transaction.
    # Opening this hot rollback set in ordinary RW SQLite performs recovery.
    child = subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=5')
db.execute('BEGIN IMMEDIATE')
if sys.argv[2] == 'True':
    for i in range(300):
        db.execute('INSERT INTO sources VALUES (?,?,?)', (str(i) + 'x' * 4000, 'permission', 'synthetic-target'))
else:
    db.execute('UPDATE user_evidence SET payload=?', ('b' * 4000,))
os._exit(77)
""", str(journal), str(configured)], capture_output=True, timeout=15)
    assert child.returncode == 77, child.stderr
    sidecar = tmp_path / (journal.name + "-journal")
    assert sidecar.exists() and sidecar.read_bytes()[:8] != b"\0" * 8
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in (journal, sidecar)}
    invoke(capsys, "observations-show", "--journal", journal, "--request-id", "request",
           "--as-of", T0, "--freshness", ttl, code=2)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in (journal, sidecar)}
