"""Synthetic offline journal tests: these are not executor proof."""

import copy
import sqlite3

import pytest

from dimaggi_receiver.observations import ObservationError, ObservationStore, SourceConflict


T0 = "2026-09-19T12:00:00Z"
T1 = "2026-09-19T12:00:01Z"
T2 = "2026-09-19T12:00:02Z"
T3 = "2026-09-19T12:00:03Z"
FRESHNESS = {"permission": 60, "attempt": 60, "workload": 60}


def intent():
    return {
        "request_id": "request-1", "report_id": "report-sha256-test",
        "profile_id": "synthetic-cpu-test", "target_id": "synthetic-target",
        "workload_id": "synthetic-namespace/job-0", "desired_state": "succeeded",
        "sources": {kind: f"synthetic-{kind}" for kind in FRESHNESS},
        "evidence_class": "synthetic",
    }


def event(kind, state, sequence=1, /, **changes):
    binding = intent()
    record = binding["request_id"] if kind == "permission" else binding["workload_id"] if kind == "workload" else "attempt-1"
    value = {
        **{key: binding[key] for key in ("request_id", "report_id", "profile_id", "target_id", "workload_id")},
        "event_id": f"{kind}-{sequence}", "kind": kind, "source_id": binding["sources"][kind],
        "source_epoch": "epoch-1", "source_record_id": record,
        "source_version": f"opaque-v{sequence}", "source_sequence": sequence,
        "observed_at_utc": T1, "object_id": None if kind == "permission" or state in {"unknown", "absent", "not_started"} else "uid-1",
        "state": state, "evidence_class": "synthetic", "payload": {},
    }
    value.update(changes)
    return value


@pytest.fixture
def store(tmp_path):
    with ObservationStore(tmp_path / "observations.sqlite") as journal:
        for kind in FRESHNESS:
            journal.register_source(f"synthetic-{kind}", kind, "synthetic-target")
        journal.register_intent(intent())
        yield journal


def project(store, **changes):
    return store.project("request-1", as_of_utc=changes.pop("as_of_utc", T3),
                         freshness_seconds=changes.pop("freshness_seconds", FRESHNESS), **changes)


def reasons(result):
    return {case["reason"] for case in result["cases"]}


def append_base(store, workload="succeeded"):
    for kind, state in (("permission", "allowed"), ("attempt", "completed"), ("workload", workload)):
        store.append(event(kind, state), recorded_at_utc=T1)


def test_restart_retains_intent_events_deduplication_and_reconciliation(tmp_path):
    path = tmp_path / "durable.sqlite"
    with ObservationStore(path) as first:
        for kind in FRESHNESS:
            first.register_source(f"synthetic-{kind}", kind, "synthetic-target")
        first.register_intent(intent())
        append_base(first)
        result = first.reconcile("request-1", as_of_utc=T3, freshness_seconds=FRESHNESS)
    with ObservationStore(path) as restarted:
        assert restarted.intent("request-1") == intent()
        assert len(restarted.history("request-1")) == 3
        assert restarted.append(event("attempt", "completed"), recorded_at_utc=T3) is False
        assert restarted.reconciliation(result["reconciliation_id"]) == result
        assert restarted.reconcile("request-1", as_of_utc=T3, freshness_seconds=FRESHNESS) == result


def test_accepted_submission_and_failed_workload_are_different_dimensions(store):
    append_base(store, "failed")
    result = project(store)
    assert result["projections"]["attempt"]["latest_records"][0]["event"]["state"] == "completed"
    assert result["projections"]["workload"]["latest_records"][0]["event"]["state"] == "failed"
    assert reasons(result) == {"desired_outcome_not_met"}
    assert result["recommendation"] == "hold"
    assert result["mutation_request"] is None


def test_successful_observed_outcome_does_not_prove_executor_or_permission(store):
    append_base(store)
    result = project(store)
    assert result["recommendation"] == "retain"
    assert result["proof_level"] == "synthetic_projection"
    assert result["execution_proven"] is False
    assert result["automatic_resubmission"] is False


def test_unknown_attempt_then_absence_never_becomes_safe_to_resubmit(store):
    store.append(event("permission", "allowed"), recorded_at_utc=T1)
    store.append(event("attempt", "unknown"), recorded_at_utc=T1)
    store.append(event("workload", "absent", observed_at_utc=T2), recorded_at_utc=T2)
    result = project(store)
    assert result["projections"]["permission"]["latest_records"][0]["event"]["state"] == "allowed"
    assert {"attempt_unknown", "desired_observed_divergence"} <= reasons(result)
    assert result["recommendation"] == "escalate"
    assert result["automatic_resubmission"] is False
    assert result["mutation_request"] is None


def test_out_of_order_late_event_preserved_without_replacing_newer_version(store):
    append_base(store)
    store.append(event("workload", "succeeded", 3, observed_at_utc=T2), recorded_at_utc=T2)
    store.append(event("workload", "running", 2), recorded_at_utc=T3)
    projection = project(store)["projections"]["workload"]
    assert projection["latest_records"][0]["event"]["source_sequence"] == 3
    assert projection["late_event_ids"] == ["workload-2"]
    assert len(store.history("request-1")) == 5


def test_source_reset_cannot_silently_replace_previous_epoch(store):
    append_base(store)
    store.append(event("workload", "running", event_id="after-reset", source_epoch="epoch-2", observed_at_utc=T2), recorded_at_utc=T2)
    result = project(store)
    projection = result["projections"]["workload"]
    assert projection["status"] == "source_reset"
    assert projection["source_epochs"] == ["epoch-1", "epoch-2"]
    assert len(projection["latest_records"]) == 2
    assert "workload_source_reset" in reasons(result)
    assert result["recommendation"] == "escalate"


@pytest.mark.parametrize("changes", [
    {"state": "failed"},
    {"event_id": "changed-version", "state": "failed"},
    {"event_id": "changed-sequence", "source_version": "different-version", "state": "failed"},
])
def test_changed_identity_version_or_sequence_is_durable_conflict(store, changes):
    append_base(store)
    changed = event("workload", "succeeded", **changes)
    with pytest.raises(SourceConflict):
        store.append(changed, recorded_at_utc=T2)
    assert len(store.history("request-1")) == 3
    assert project(store)["projections"]["workload"]["latest_records"][0]["event"]["state"] == "succeeded"
    assert "source_conflict" in reasons(project(store))
    assert project(store, as_of_utc=T1)["cases"] == []


def test_same_source_version_under_new_transport_event_id_is_idempotent(store):
    store.append(event("workload", "running"), recorded_at_utc=T1)
    assert store.append(event("workload", "running", event_id="transport-retry"), recorded_at_utc=T2) is False
    assert len(store.history("request-1")) == 1


@pytest.mark.parametrize("field,value", [
    ("target_id", "other-target"), ("request_id", "other-request"),
    ("report_id", "other-report"), ("profile_id", "weaker-profile"),
    ("workload_id", "other-workload"), ("source_id", "foreign-controller"),
    ("evidence_class", "observed"), ("source_record_id", "other-record"),
])
def test_foreign_binding_is_rejected_without_rewriting_intent(store, field, value):
    with pytest.raises(ObservationError):
        store.append(event("workload", "running", **{field: value}), recorded_at_utc=T1)
    assert store.history("request-1") == []
    assert store.intent("request-1") == intent()


def test_object_name_reuse_requires_reconciliation_not_silent_uid_change(store):
    append_base(store)
    store.append(event("workload", "deleted", 2, observed_at_utc=T2), recorded_at_utc=T2)
    store.append(event("workload", "running", 3, object_id="uid-2", observed_at_utc=T3), recorded_at_utc=T3)
    result = project(store)
    assert {"object_identity_reuse_or_conflict", "object_not_attributed_to_submission"} <= reasons(result)
    assert result["recommendation"] == "escalate"
    assert result["mutation_request"] is None


def test_explicit_foreign_writer_change_is_retained(store):
    append_base(store)
    store.append(event("workload", "running", 2, payload={"foreign_change": True, "writer": "synthetic-other"}), recorded_at_utc=T2)
    assert "foreign_change" in reasons(project(store))


def test_deleted_job_is_desired_observed_divergence_without_recreation(store):
    append_base(store)
    store.append(event("workload", "deleted", 2, observed_at_utc=T2), recorded_at_utc=T2)
    result = project(store)
    assert reasons(result) == {"desired_observed_divergence"}
    assert result["recommendation"] == "hold"
    assert result["automatic_resubmission"] is False


def test_all_attempts_retained_and_earlier_failed_attempt_not_erased(store):
    store.append(event("attempt", "failed", object_id=None), recorded_at_utc=T1)
    store.append(event("attempt", "completed", 2, source_record_id="attempt-2"), recorded_at_utc=T2)
    attempts = project(store)["projections"]["attempt"]["latest_records"]
    assert {row["event"]["state"] for row in attempts} == {"failed", "completed"}
    assert len(store.history("request-1")) == 2


def test_missing_stale_and_unknown_remain_distinct(store):
    store.append(event("attempt", "unknown"), recorded_at_utc=T1)
    store.append(event("workload", "unknown", observed_at_utc=T2), recorded_at_utc=T2)
    result = project(store, freshness_seconds={"permission": 60, "attempt": 1, "workload": 60})
    assert result["projections"]["permission"]["status"] == "missing"
    assert result["projections"]["attempt"]["status"] == "stale"
    assert result["projections"]["attempt"]["latest_records"][0]["event"]["state"] == "unknown"
    assert result["projections"]["workload"]["status"] == "current"
    assert "workload_unknown" in reasons(result)


def test_freshness_expires_at_equality(store):
    append_base(store)
    assert project(store, freshness_seconds={k: 2 for k in FRESHNESS})["projections"]["workload"]["status"] == "stale"
    assert project(store, freshness_seconds={k: 2.000001 for k in FRESHNESS})["projections"]["workload"]["status"] == "current"


def test_historical_projection_cannot_see_later_ingestion(store):
    store.append(event("workload", "running", observed_at_utc=T0), recorded_at_utc=T2)
    assert project(store, as_of_utc=T1)["projections"]["workload"]["status"] == "missing"
    assert project(store, as_of_utc=T2)["projections"]["workload"]["status"] == "current"


@pytest.mark.parametrize("freshness", [
    {}, {"permission": 1, "attempt": 1},
    *[{"permission": value, "attempt": 1, "workload": 1} for value in (0, -1, True, float("nan"), float("inf"), "60", None)],
])
def test_no_missing_or_invented_freshness_default(store, freshness):
    with pytest.raises(ObservationError):
        project(store, freshness_seconds=freshness)


@pytest.mark.parametrize("field,value", [
    ("source_sequence", True), ("source_sequence", -1), ("source_sequence", 2**63),
    ("source_sequence", 1.0), ("event_id", ""), ("source_version", " 1"),
    ("kind", []), ("state", []), ("state", "allowed"),
    ("observed_at_utc", "2026-09-19T12:00:01.0000001Z"),
    ("observed_at_utc", "2026-09-19T12:00:01+00:00"),
    ("observed_at_utc", "2026-02-30T12:00:00Z"),
    ("observed_at_utc", T2),
    ("payload", {"x": float("nan")}), ("payload", {"x": float("inf")}),
    ("payload", {"x": (1, 2)}), ("payload", {1: "coerced"}),
    ("payload", {"foreign_change": "true"}), ("object_id", None),
])
def test_invalid_inputs_cannot_become_observations(store, field, value):
    with pytest.raises(ObservationError):
        store.append(event("workload", "running", **{field: value}), recorded_at_utc=T1)
    assert store.history("request-1") == []


def test_attempt_acknowledgement_without_object_identity_is_invalid(store):
    with pytest.raises(ObservationError):
        store.append(event("attempt", "completed", object_id=None), recorded_at_utc=T1)


def test_permission_cannot_claim_to_own_scheduler_object(store):
    with pytest.raises(ObservationError):
        store.append(event("permission", "allowed", object_id="uid-1"), recorded_at_utc=T1)


def test_request_id_and_source_role_are_immutable(store):
    assert store.register_intent(intent()) is False
    changed = copy.deepcopy(intent())
    changed["report_id"] = "different-report"
    with pytest.raises(SourceConflict):
        store.register_intent(changed)
    with pytest.raises(SourceConflict):
        store.register_source("synthetic-attempt", "workload", "synthetic-target")


@pytest.mark.parametrize("table", ["intents", "sources", "events", "conflicts", "reconciliations", "triage"])
def test_sqlite_journal_tables_reject_updates_and_deletes(store, table):
    append_base(store)
    with pytest.raises(SourceConflict):
        store.append(event("workload", "failed"), recorded_at_utc=T2)
    result = store.reconcile("request-1", as_of_utc=T3, freshness_seconds=FRESHNESS)
    store.record_triage(triage_id="triage-1", reconciliation_id=result["reconciliation_id"],
                        case_id=result["cases"][0]["case_id"], actor_id="synthetic-reviewer",
                        disposition="hold", reason="Retain imported uncertainty", recorded_at_utc=T3)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(f"DELETE FROM {table}")
    columns = store.db.execute(f"PRAGMA table_info({table})").fetchall()
    column = columns[0]["name"]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute(f"UPDATE {table} SET {column}={column}")


def test_case_age_uses_actual_utc_order_with_fractional_timestamps(store):
    append_base(store)
    store.append(event("workload", "running", 2, object_id="uid-2", observed_at_utc="2026-09-19T12:00:01.1Z"), recorded_at_utc="2026-09-19T12:00:01.1Z")
    case = next(item for item in project(store)["cases"] if item["reason"] == "object_identity_reuse_or_conflict")
    assert case["first_supported_at_utc"] == "2026-09-19T12:00:01.1Z"
    assert case["age_seconds"] == 1.9


def test_durable_triage_cannot_clear_unknown_or_supply_permission(tmp_path):
    path = tmp_path / "triage.sqlite"
    with ObservationStore(path) as journal:
        for kind in FRESHNESS:
            journal.register_source(f"synthetic-{kind}", kind, "synthetic-target")
        journal.register_intent(intent())
        journal.append(event("attempt", "unknown"), recorded_at_utc=T1)
        result = journal.reconcile("request-1", as_of_utc=T2, freshness_seconds=FRESHNESS)
        case = next(case for case in result["cases"] if case["reason"] == "attempt_unknown")
        note = dict(triage_id="note-1", reconciliation_id=result["reconciliation_id"], case_id=case["case_id"],
                    actor_id="synthetic-reviewer", disposition="proposal", reason="Request read-only target reconciliation", recorded_at_utc=T3)
        assert journal.record_triage(**note) is True
        assert journal.record_triage(**note) is False
        with pytest.raises(SourceConflict):
            journal.record_triage(**{**note, "reason": "Changed rationale"})
        assert journal.project("request-1", as_of_utc=T2, freshness_seconds=FRESHNESS)["cases"] == result["cases"]
    with ObservationStore(path) as restarted:
        assert len(restarted.triage_history(case["case_id"])) == 1
        current = project(restarted)
        unknown = next(item for item in current["cases"] if item["reason"] == "attempt_unknown")
        assert unknown["triage"] == "recorded"
        assert unknown["disposition"] == "escalate"
        assert unknown["triage_events"][0]["grants_permission"] is False
        assert current["recommendation"] == "escalate"
        assert current["mutation_request"] is None
        assert restarted.reconciliation(result["reconciliation_id"]) == result


@pytest.mark.parametrize("change", [
    {"case_id": "unrelated-case"}, {"disposition": "closed"},
    {"disposition": "allow"}, {"recorded_at_utc": T0}, {"reason": ""},
])
def test_triage_cannot_rebind_close_authorize_or_precede_case(store, change):
    result = store.reconcile("request-1", as_of_utc=T2, freshness_seconds=FRESHNESS)
    note = dict(triage_id="note-1", reconciliation_id=result["reconciliation_id"], case_id=result["cases"][0]["case_id"],
                actor_id="synthetic-reviewer", disposition="hold", reason="Waiting for evidence", recorded_at_utc=T3)
    with pytest.raises(ObservationError):
        store.record_triage(**{**note, **change})


def test_refresh_cannot_clear_source_reset_or_immutable_conflict(store):
    append_base(store)
    store.append(event("workload", "running", event_id="after-reset", source_epoch="epoch-2"), recorded_at_utc=T2)
    with pytest.raises(SourceConflict):
        store.append(event("attempt", "failed"), recorded_at_utc=T2)
    store.append(event("workload", "succeeded", 2, source_epoch="epoch-2", observed_at_utc=T3), recorded_at_utc=T3)
    store.append(event("attempt", "completed", 2, observed_at_utc=T3), recorded_at_utc=T3)
    result = project(store)
    assert {"source_conflict", "workload_source_reset"} <= reasons(result)
    assert result["recommendation"] == "escalate"


def test_stale_case_age_begins_at_expiry_not_original_observation(store):
    append_base(store)
    result = project(store, as_of_utc="2026-09-19T12:02:01Z")
    stale = next(case for case in result["cases"] if case["reason"] == "workload_stale")
    assert stale["first_supported_at_utc"] == "2026-09-19T12:01:01.000000Z"
    assert stale["age_seconds"] == 60


def test_stale_case_age_cannot_predate_late_collection(store):
    store.append(event("workload", "unknown"), recorded_at_utc="2026-09-19T12:02:00Z")
    result = project(store, as_of_utc="2026-09-19T12:02:01Z")
    stale = next(case for case in result["cases"] if case["reason"] == "workload_stale")
    assert stale["first_supported_at_utc"] == "2026-09-19T12:02:00.000000Z"
    assert stale["age_seconds"] == 1


def test_missing_case_without_observed_start_has_unknown_age(store):
    for as_of in (T1, T3):
        result = project(store, as_of_utc=as_of)
        assert all(case["first_supported_at_utc"] is None and case["age_seconds"] is None for case in result["cases"])


def test_large_integer_ttl_has_no_float_overflow_or_invented_expiry(store):
    append_base(store)
    result = project(store, freshness_seconds={kind: 10**1000 for kind in FRESHNESS})
    assert all(part["status"] == "current" for part in result["projections"].values())


def test_submicrosecond_ttl_uses_first_representable_expiry_in_case_time(store):
    append_base(store)
    result = project(store, as_of_utc="2026-09-19T12:00:01.000001Z", freshness_seconds={kind: 0.0000001 for kind in FRESHNESS})
    stale = next(case for case in result["cases"] if case["reason"] == "workload_stale")
    assert stale["first_supported_at_utc"] == "2026-09-19T12:00:01.000001Z"
    assert stale["age_seconds"] == 0


def test_attempt_stream_cannot_move_between_request_identities(store):
    store.append(event("attempt", "unknown"), recorded_at_utc=T1)
    second = intent()
    second.update(request_id="request-2", report_id="report-2")
    store.register_intent(second)
    changed = event("attempt", "completed", 2, request_id="request-2", report_id="report-2")
    with pytest.raises(SourceConflict, match="stream rebound"):
        store.append(changed, recorded_at_utc=T2)
    result = store.project("request-2", as_of_utc=T3, freshness_seconds=FRESHNESS)
    assert "source_conflict" in reasons(result)
    assert store.history("request-2") == []
    assert store.history("request-1")[0]["event"]["state"] == "unknown"


def test_immutable_object_cannot_be_attributed_to_two_requests(store):
    append_base(store)
    second = intent()
    second.update(request_id="request-2", workload_id="synthetic-namespace/job-1")
    store.register_intent(second)
    changed = event("attempt", "completed", 2, request_id="request-2", workload_id=second["workload_id"], source_record_id="attempt-2")
    with pytest.raises(SourceConflict, match="already attributed"):
        store.append(changed, recorded_at_utc=T2)
    result = store.project("request-2", as_of_utc=T3, freshness_seconds=FRESHNESS)
    assert "source_conflict" in reasons(result)
    assert store.history("request-2") == []
