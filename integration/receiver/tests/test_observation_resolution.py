"""Synthetic application lifecycle checks; no scheduler or executor proof."""

import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys

import pytest

import dimaggi_receiver.observations as observations
from dimaggi_receiver.observations import ObservationError, ObservationStore, SourceConflict


T1 = "2026-09-19T12:00:01Z"
T2 = "2026-09-19T12:00:02Z"
T3 = "2026-09-19T12:00:03Z"
OLD = "2026-09-19T12:01:02Z"
NEW = "2026-09-19T12:01:03Z"
LATER = "2026-09-19T12:01:04Z"
TTL = {"permission": 60, "attempt": 60, "workload": 60}


def intent(request="request-1"):
    return {
        "request_id": request, "report_id": "synthetic-report", "profile_id": "synthetic-profile",
        "target_id": "synthetic-target", "workload_id": "ns/synthetic-job", "desired_state": "succeeded",
        "sources": {kind: f"synthetic-{kind}" for kind in TTL}, "evidence_class": "synthetic",
    }


def event(kind, state, sequence=1, **changes):
    binding = intent()
    value = {key: binding[key] for key in observations.IDENTITIES}
    value.update(
        event_id=f"{kind}-{sequence}", kind=kind, source_id=binding["sources"][kind],
        source_epoch="epoch-1", source_record_id=binding["request_id"] if kind == "permission" else binding["workload_id"] if kind == "workload" else "attempt-1",
        source_version=f"v{sequence}", source_sequence=sequence, observed_at_utc=T1,
        object_id=None if kind == "permission" or state in {"unknown", "absent"} else "synthetic-uid",
        state=state, evidence_class="synthetic", payload={},
    )
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path):
    with ObservationStore(tmp_path / "observations.sqlite") as journal:
        for kind in TTL:
            journal.register_source(f"synthetic-{kind}", kind, "synthetic-target")
        journal.register_intent(intent())
        yield journal


def base(store):
    for kind, state in (("permission", "allowed"), ("attempt", "completed"), ("workload", "succeeded")):
        store.append(event(kind, state), recorded_at_utc=T1)


def snapshot(store, at, **changes):
    return store.reconcile("request-1", as_of_utc=at, freshness_seconds=changes.get("freshness_seconds", TTL))


def note(opening, resolving, condition="workload_stale", **changes):
    case = next(case for case in opening["cases"] if case["reason"] == condition)
    result = dict(resolution_id="resolution-1", opening_reconciliation_id=opening["reconciliation_id"],
                  resolving_reconciliation_id=resolving["reconciliation_id"], case_id=case["case_id"],
                  actor_id="synthetic-reviewer", reason="Fresh source observation reviewed",
                  recorded_at_utc=LATER)
    result.update(changes)
    return result


def cleared_staleness(store):
    base(store)
    opening = snapshot(store, OLD)
    store.append(event("workload", "succeeded", 2, observed_at_utc=NEW), recorded_at_utc=NEW)
    return opening, snapshot(store, LATER)


def subprocess_environment():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(observations.__file__).resolve().parent.parent)
    return env


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_actual_inode_has_one_writer_across_processes_and_aliases(tmp_path, alias):
    path = tmp_path / "journal.sqlite"
    with ObservationStore(path):
        other = path
        if alias != "same":
            other = tmp_path / alias
            os.symlink(path, other) if alias == "symlink" else os.link(path, other)
        run = subprocess.run([sys.executable, "-c", """
import sys
from dimaggi_receiver.observations import ObservationStore, ObservationError
try:
    with ObservationStore(sys.argv[1]):
        raise AssertionError('second writer acquired the same inode')
except ObservationError as exc:
    assert 'active application writer' in str(exc)
    print('writer refused')
""", str(other)], env=subprocess_environment(), capture_output=True, text=True, timeout=10)
        assert run.returncode == 0, run.stderr
        assert run.stdout.strip() == "writer refused"
    with ObservationStore(other) as reopened:
        assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_same_process_cannot_open_second_writer_and_close_is_idempotent(tmp_path):
    path = tmp_path / "journal.sqlite"
    store = ObservationStore(path)
    try:
        with pytest.raises(ObservationError, match="active application writer"):
            ObservationStore(path)
    finally:
        store.close()
        store.close()
    with ObservationStore(path):
        pass


def test_crashed_writer_releases_lock_and_committed_data_survives(tmp_path):
    path = tmp_path / "journal.sqlite"
    child = subprocess.Popen([sys.executable, "-c", """
import sys, time
from dimaggi_receiver.observations import ObservationStore
store = ObservationStore(sys.argv[1])
store.register_source('synthetic-source', 'workload', 'synthetic-target')
print('committed', flush=True)
time.sleep(30)
""", str(path)], env=subprocess_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0], "child did not initialize"
        assert child.stdout.readline().strip() == "committed"
        with pytest.raises(ObservationError, match="active application writer"):
            ObservationStore(path)
        child.kill()
        child.wait(timeout=10)
        with ObservationStore(path) as reopened:
            assert reopened.register_source("synthetic-source", "workload", "synthetic-target") is False
            assert reopened.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_failed_initialization_releases_inode_lock(tmp_path, monkeypatch):
    path = tmp_path / "journal.sqlite"
    original = ObservationStore._initialize
    def fail(self):
        raise RuntimeError("synthetic initialization failure")
    monkeypatch.setattr(ObservationStore, "_initialize", fail)
    with pytest.raises(RuntimeError, match="synthetic initialization failure"):
        ObservationStore(path)
    monkeypatch.setattr(ObservationStore, "_initialize", original)
    with ObservationStore(path):
        pass


def test_memory_stores_are_independent_not_a_persistent_shared_journal():
    with ObservationStore(":memory:") as first, ObservationStore(":memory:") as second:
        assert first.register_source("same", "workload", "target-1") is True
        assert second.register_source("same", "attempt", "target-2") is True
    with pytest.raises(ObservationError, match="persistent file path"):
        ObservationStore("")


def test_resolution_is_durable_idempotent_and_preserves_sources_snapshots_and_cases(store):
    opening, resolving = cleared_staleness(store)
    before_history = store.history("request-1")
    arguments = note(opening, resolving)
    assert store.record_resolution(**arguments) is True
    assert store.record_resolution(**arguments) is False
    assert store.history("request-1") == before_history
    assert store.reconciliation(opening["reconciliation_id"]) == opening
    assert snapshot(store, LATER) == resolving
    notes = store.resolution_history(arguments["case_id"])
    assert len(notes) == 1
    assert notes[0]["disposition"] == "resolved_in_read_projection"
    assert notes[0]["mutation_request"] is None
    assert notes[0]["grants_permission"] is False
    assert notes[0]["resolves_attempt_effects"] is False
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    store.close()
    with ObservationStore(path) as reopened:
        assert reopened.resolution_history(arguments["case_id"]) == notes
        assert reopened.record_resolution(**arguments) is False
        assert reopened.reconciliation(opening["reconciliation_id"]) == opening


@pytest.mark.parametrize("field,value", [
    ("actor_id", "different-reviewer"), ("reason", "changed reason"),
    ("case_id", "other-case"), ("recorded_at_utc", NEW),
    ("opening_reconciliation_id", "other-snapshot"),
    ("resolving_reconciliation_id", "other-snapshot"),
])
def test_resolution_identity_cannot_rebind_any_exact_input(store, field, value):
    opening, resolving = cleared_staleness(store)
    arguments = note(opening, resolving)
    store.record_resolution(**arguments)
    with pytest.raises(SourceConflict, match="resolution identity"):
        store.record_resolution(**{**arguments, field: value})


def test_new_case_id_with_same_stale_condition_cannot_close_prior_case(store):
    base(store)
    opening = snapshot(store, OLD)
    store.append(event("workload", "succeeded", 2, observed_at_utc=T2), recorded_at_utc=NEW)
    resolving = snapshot(store, LATER)
    arguments = note(opening, resolving)
    assert arguments["case_id"] not in {case["case_id"] for case in resolving["cases"]}
    with pytest.raises(ObservationError, match="semantic condition is still active"):
        store.record_resolution(**arguments)
    assert store.resolution_history(arguments["case_id"]) == []


def test_relaxing_freshness_policy_cannot_manufacture_resolution(store):
    base(store)
    opening = snapshot(store, OLD)
    resolving = snapshot(store, LATER, freshness_seconds={kind: 3600 for kind in TTL})
    with pytest.raises(ObservationError, match="freshness policy"):
        store.record_resolution(**note(opening, resolving))


def test_missing_cannot_be_closed_by_replacing_it_with_stale_evidence(store):
    opening = snapshot(store, T1)
    store.append(event("workload", "unknown"), recorded_at_utc=NEW)
    resolving = snapshot(store, LATER)
    with pytest.raises(ObservationError, match="current relevant source"):
        store.record_resolution(**note(opening, resolving, "workload_missing"))


def test_same_snapshot_and_note_predating_snapshot_are_rejected(store):
    opening, resolving = cleared_staleness(store)
    with pytest.raises(ObservationError, match="later snapshot"):
        store.record_resolution(**note(opening, opening))
    with pytest.raises(ObservationError, match="later snapshot"):
        store.record_resolution(**note(opening, resolving, recorded_at_utc=NEW))


def test_snapshots_from_different_requests_cannot_resolve_case(store):
    opening, _ = cleared_staleness(store)
    store.register_intent(intent("request-2"))
    foreign = store.reconcile("request-2", as_of_utc=LATER, freshness_seconds=TTL)
    with pytest.raises(ObservationError, match="same immutable intent"):
        store.record_resolution(**note(opening, foreign))


def test_unknown_attempt_cannot_be_closed_by_application_note(store):
    store.append(event("attempt", "unknown"), recorded_at_utc=T1)
    opening = snapshot(store, T1)
    store.append(event("attempt", "completed", 2, observed_at_utc=T2), recorded_at_utc=T2)
    resolving = snapshot(store, T3)
    with pytest.raises(ObservationError, match="source or authority resolution contract"):
        store.record_resolution(**note(opening, resolving, "attempt_unknown", recorded_at_utc=T3))
    assert store.history("request-1")[0]["event"]["state"] == "unknown"


@pytest.mark.parametrize("issue", ["conflict", "reset", "identity"])
def test_source_or_identity_issue_blocks_resolution_even_if_stale_condition_cleared(store, issue):
    opening, _ = cleared_staleness(store)
    if issue == "conflict":
        with pytest.raises(SourceConflict):
            store.append(event("attempt", "failed"), recorded_at_utc=LATER)
    elif issue == "reset":
        store.append(event("permission", "allowed", 2, source_epoch="epoch-2", observed_at_utc=LATER), recorded_at_utc=LATER)
    else:
        store.append(event("workload", "succeeded", 3, object_id="other-uid", observed_at_utc=LATER), recorded_at_utc=LATER)
    resolving = snapshot(store, LATER)
    with pytest.raises(ObservationError, match="source or object identity"):
        store.record_resolution(**note(opening, resolving))


def test_late_conflict_cannot_be_hidden_by_an_old_clear_snapshot(store):
    opening, resolving = cleared_staleness(store)
    with pytest.raises(SourceConflict):
        store.append(event("attempt", "failed"), recorded_at_utc="2026-09-19T12:01:05Z")
    with pytest.raises(ObservationError, match="source or object identity"):
        store.record_resolution(**note(opening, resolving, recorded_at_utc="2026-09-19T12:01:06Z"))


def test_resolution_does_not_hide_recurrence_or_later_conflict(store):
    opening, resolving = cleared_staleness(store)
    arguments = note(opening, resolving)
    store.record_resolution(**arguments)
    before = store.resolution_history(arguments["case_id"])
    with pytest.raises(SourceConflict):
        store.append(event("attempt", "failed"), recorded_at_utc="2026-09-19T12:01:05Z")
    later = snapshot(store, "2026-09-19T12:03:00Z")
    assert {"source_conflict", "workload_stale"} <= {case["reason"] for case in later["cases"]}
    assert later["recommendation"] == "escalate"
    assert later["mutation_request"] is None
    assert store.resolution_history(arguments["case_id"]) == before
    assert store.record_resolution(**arguments) is False


def test_resolution_table_is_append_only(store):
    opening, resolving = cleared_staleness(store)
    store.record_resolution(**note(opening, resolving))
    for statement in ("DELETE FROM resolutions", "UPDATE resolutions SET case_id='other'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only journal"):
            store.db.execute(statement)


def test_closing_other_descriptors_cannot_release_open_file_description_lock(tmp_path):
    path = tmp_path / "journal.sqlite"
    with ObservationStore(path):
        raw = os.open(path, os.O_RDONLY)
        os.close(raw)
        with sqlite3.connect(path) as reader:
            assert reader.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        reader.close()
        run = subprocess.run([sys.executable, "-c", """
import sys
from dimaggi_receiver.observations import ObservationStore, ObservationError
try:
    ObservationStore(sys.argv[1])
except ObservationError as exc:
    assert 'active application writer' in str(exc)
else:
    raise AssertionError('closing an unrelated descriptor released writer ownership')
""", str(path)], env=subprocess_environment(), capture_output=True, text=True, timeout=10)
        assert run.returncode == 0, run.stderr


def test_unsupported_platform_refuses_file_store_without_creating_file(tmp_path, monkeypatch):
    path = tmp_path / "unsupported.sqlite"
    monkeypatch.setattr(observations.sys, "platform", "unsupported-test-platform")
    with pytest.raises(ObservationError, match="Darwin OFD"):
        ObservationStore(path)
    assert not path.exists()
    with ObservationStore(":memory:"):
        pass


@pytest.mark.parametrize("condition", sorted(observations.RESOLVABLE_CONDITIONS))
def test_each_allowlisted_condition_can_clear_only_in_the_later_read_projection(store, condition):
    kind = observations.RESOLVABLE_CONDITIONS[condition]
    states = {"permission": "allowed", "attempt": "completed", "workload": "succeeded"}
    if condition.endswith("_missing"):
        for role, state in states.items():
            if role != kind:
                store.append(event(role, state), recorded_at_utc=T1)
        opening = snapshot(store, T1)
        store.append(event(kind, states[kind], observed_at_utc=T2), recorded_at_utc=T2)
        resolving = snapshot(store, T3)
        recorded = T3
    elif condition.endswith("_stale"):
        base(store)
        opening = snapshot(store, OLD)
        store.append(event(kind, states[kind], 2, observed_at_utc=NEW), recorded_at_utc=NEW)
        resolving = snapshot(store, LATER)
        recorded = LATER
    else:
        for role in ("permission", "attempt"):
            store.append(event(role, states[role]), recorded_at_utc=T1)
        state = {"workload_unknown": "unknown", "desired_outcome_not_met": "failed", "desired_observed_divergence": "absent"}[condition]
        store.append(event("workload", state), recorded_at_utc=T1)
        opening = snapshot(store, T1)
        store.append(event("workload", "succeeded", 2, observed_at_utc=T2), recorded_at_utc=T2)
        resolving = snapshot(store, T3)
        recorded = T3
    arguments = note(opening, resolving, condition, recorded_at_utc=recorded)
    assert store.record_resolution(**arguments) is True
    assert store.resolution_history(arguments["case_id"])[0]["condition"] == condition
    assert store.reconciliation(opening["reconciliation_id"]) == opening
    assert resolving["mutation_request"] is None


def test_resolving_missing_permission_does_not_resolve_unknown_attempt(store):
    store.append(event("attempt", "unknown"), recorded_at_utc=T1)
    opening = snapshot(store, T1)
    store.append(event("permission", "allowed", observed_at_utc=T2), recorded_at_utc=T2)
    resolving = snapshot(store, T3)
    arguments = note(opening, resolving, "permission_missing", recorded_at_utc=T3)
    assert store.record_resolution(**arguments) is True
    assert "attempt_unknown" in {case["reason"] for case in snapshot(store, T3)["cases"]}
    assert store.history("request-1")[0]["event"]["state"] == "unknown"


def test_clear_snapshot_cannot_be_used_after_its_evidence_expires(store):
    opening, resolving = cleared_staleness(store)
    with pytest.raises(ObservationError, match="semantic condition is still active"):
        store.record_resolution(**note(opening, resolving, recorded_at_utc="2026-09-19T12:03:00Z"))
