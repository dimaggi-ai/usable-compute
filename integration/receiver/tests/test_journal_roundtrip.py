"""Real Go journal process -> installed Python import -> restarted read model.

Set DIMAGGI_BATCH_JOURNAL to the compiled synthetic command. No scheduler runs.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from dimaggi_receiver.journal_import import import_journal
from dimaggi_receiver.observations import ObservationStore


T0 = "2026-09-19T12:00:00Z"
T1 = "2026-09-19T12:00:01Z"
T2 = "2026-09-19T12:00:02Z"
EXPIRY = "2026-09-19T13:00:00Z"
TTL = {"permission": 60, "attempt": 60, "workload": 60}


def run_journal(tmp_path, scenario, now=T0):
    binary = os.environ.get("DIMAGGI_BATCH_JOURNAL")
    if not binary:
        pytest.skip("compiled TENWA batch-journal-demo required")
    process = subprocess.run([
        binary, "--journal", str(tmp_path / "authority.jsonl"),
        "--scenario", scenario, "--now", now,
        "--expires", T0 if scenario == "expired" else EXPIRY,
    ], text=True, capture_output=True, timeout=15)
    assert process.returncode == 0, process.stdout + process.stderr
    return process.stdout, json.loads(process.stdout)


def register(store, exported):
    source = exported["journal_id"]
    snapshot = exported["records"][0]["snapshot"]
    binding = snapshot["input"]["report_binding"]
    intent = {
        "request_id": snapshot["request_id"], "report_id": binding["report_id"],
        "profile_id": binding["profile_id"], "target_id": binding["target_scope"],
        "workload_id": binding["selected_request"], "desired_state": "succeeded",
        "sources": {kind: source + "/" + kind for kind in TTL},
        "evidence_class": "synthetic",
    }
    for kind, source_id in intent["sources"].items():
        store.register_source(source_id, kind, intent["target_id"])
    store.register_intent(intent)
    return intent


@pytest.mark.parametrize("scenario,expected", [
    ("success", "submitted"), ("lost-response", "unknown"),
    ("transport-error", "unknown"), ("denied", "not_started"),
    ("absence", "unknown"), ("matching-object", "submitted"),
    ("expired", "not_started"),
])
def test_real_export_import_and_duplicate_after_restart(tmp_path, scenario, expected):
    raw, exported = run_journal(tmp_path, scenario)
    path = tmp_path / "application.sqlite"
    with ObservationStore(path) as store:
        intent = register(store, exported)
        summary = import_journal(store, raw, request_id=intent["request_id"],
                                 expected_journal_id=exported["journal_id"], recorded_at_utc=T1)
        assert summary["imported_events"] == len(exported["records"]) + 1
        assert summary["permission"] == "not_granted"
        result = store.reconcile(intent["request_id"], as_of_utc=T1, freshness_seconds=TTL)
        attempt = result["projections"]["attempt"]["latest_records"][0]["event"]
        assert attempt["state"] == expected
        assert result["projections"]["permission"]["latest_records"][0]["event"]["state"] == "unresolved"
        assert result["projections"]["workload"]["status"] == "missing"
        assert result["execution_proven"] is False
        assert result["mutation_request"] is None
        history = store.history(intent["request_id"])
    with ObservationStore(path) as restarted:
        duplicate = import_journal(restarted, raw, request_id=intent["request_id"],
                                   expected_journal_id=exported["journal_id"], recorded_at_utc=T2)
        assert duplicate["imported_events"] == 0
        assert restarted.history(intent["request_id"]) == history
        stale = restarted.project(intent["request_id"], as_of_utc="2026-09-19T12:01:00Z", freshness_seconds=TTL)
        assert stale["projections"]["attempt"]["status"] == "stale"


@pytest.mark.parametrize("scenario", ["prepare-only", "dispatch-only"])
def test_process_recovery_becomes_unknown_in_application(tmp_path, scenario):
    original_raw, initial = run_journal(tmp_path, scenario)
    recovered_raw, recovered = run_journal(tmp_path, "recover", now=T1)
    assert recovered["records"][-1]["snapshot"]["state"] == "unknown"
    with ObservationStore(tmp_path / "application.sqlite") as store:
        intent = register(store, initial)
        kwargs = {"request_id": intent["request_id"], "expected_journal_id": initial["journal_id"]}
        import_journal(store, original_raw, recorded_at_utc=T0, **kwargs)
        import_journal(store, recovered_raw, recorded_at_utc=T1, **kwargs)
        projected = store.project(intent["request_id"], as_of_utc=T2, freshness_seconds=TTL)
        assert "attempt_unknown" in {c["reason"] for c in projected["cases"]}
        assert projected["recommendation"] == "escalate"
        assert projected["automatic_resubmission"] is False
        assert projected["projections"]["workload"]["status"] == "missing"


def test_installed_cli_import_reconcile_and_inspect(tmp_path):
    """Separate processes consume actual Go bytes and retain original freshness."""
    raw, exported = run_journal(tmp_path, "prepare-only")
    authority = tmp_path / "authority.jsonl"
    original = authority.read_bytes()
    inspected = subprocess.run([
        os.environ["DIMAGGI_BATCH_JOURNAL"], "--journal", str(authority),
        "--scenario", "inspect",
    ], text=True, capture_output=True, timeout=15)
    assert inspected.returncode == 0, inspected.stderr
    assert json.loads(inspected.stdout) == exported
    assert authority.read_bytes() == original
    with ObservationStore(":memory:") as reference:
        intent = register(reference, exported)
    config = tmp_path / "intent.json"
    config.write_text(json.dumps(intent))
    inputs = tmp_path / "export.json"
    inputs.write_text(raw)
    freshness = tmp_path / "freshness.json"
    freshness.write_text(json.dumps(TTL))
    application = tmp_path / "application.sqlite"
    executable = str(Path(sys.executable).parent / "dimaggi-receiver")

    def cli(command, *args):
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        process = subprocess.run([executable, command, "--journal", str(application), *args],
                                 cwd=tmp_path, env=environment, text=True,
                                 capture_output=True, timeout=15)
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)

    assert cli("observations-init", "--intent", str(config))["execution_authorized"] is False
    arguments = ["--request-id", intent["request_id"], "--input", str(inputs),
                 "--expected-journal-id", exported["journal_id"]]
    imported = cli("journal-import", *arguments, "--recorded-at", T1)
    assert imported["imported_events"] == 2
    assert cli("journal-import", *arguments, "--recorded-at", T2)["imported_events"] == 0
    before = application.read_bytes()
    result = cli("observations-show", "--request-id", intent["request_id"],
                 "--as-of", T2, "--freshness", str(freshness))
    assert application.read_bytes() == before
    assert result["execution_proven"] is False
    assert result["projections"]["workload"]["status"] == "missing"
    recorded = cli("observations-reconcile", "--request-id", intent["request_id"],
                   "--as-of", T2, "--freshness", str(freshness))
    assert recorded["reconciliation_id"]
    assert cli("observations-history", "--request-id", intent["request_id"])["events"]
