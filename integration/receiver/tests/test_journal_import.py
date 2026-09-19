"""Synthetic adapter vectors; the parent integration test uses real Go exports."""
import base64
import copy
import hashlib
import json

import pytest

from dimaggi_receiver.journal_import import import_journal, journal_events
from dimaggi_receiver.observations import ObservationError, ObservationStore, SourceConflict


JOURNAL = "synthetic-journal-test"
T0 = "2026-09-19T12:00:00Z"
T9 = "2026-09-19T12:00:09Z"
FRESHNESS = {kind: 60 for kind in ("permission", "attempt", "workload")}


def sha(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(",", ":"))


def export(states=("prepared", "dispatching", "unknown", "unknown", "acknowledged")):
    expiry = "2026-09-19T12:00:01Z" if len(states) > 1 and states[-1] == "not_sent" else "2026-09-19T12:01:00Z"
    report = {
        "schema_version": "dimaggi-receiver-report/v1", "report_id": "synthetic-report", "profile_id": "synthetic-profile/v1",
        "profile_digest": sha(b"profile"), "evidence_class": "synthetic_contract_fixture", "evidence_digest": sha(b"evidence"),
        "selected_request": "synthetic-job-0", "execution_readiness": "feasible",
        "action_intent": {"action_class": "submit_bounded_batch", "cardinality": 1,
                          "designated_scheduler_scope": "synthetic://test/not-a-cluster", "workload_artifact_digest": sha(b"payload")},
        "mutation_request": None,
    }
    raw = encoded(report).encode()
    binding = {key: report[key] for key in ("report_id", "profile_id", "profile_digest", "evidence_class", "evidence_digest", "selected_request", "execution_readiness")}
    binding.update(report_digest=sha(raw), action_class="submit_bounded_batch", cardinality=1,
                   target_scope=report["action_intent"]["designated_scheduler_scope"], payload_digest=sha(b"payload"))
    digest = sha(b"binding")
    operation = sha(b"operation")
    supplied = {
        "schema": "dimaggi-batch-preview/v1", "request_id": "request-1", "report_binding": binding,
        "expected_binding": copy.deepcopy(binding),
        "approval": {"status": "synthetic_granted", "binding_digest": digest, "expires_at_utc": expiry},
        "evidence_expires_at_utc": expiry,
    }
    preview = {
        "schema": "dimaggi-batch-preview-result/v1", "contract": "synthetic-lab-test", "evaluated_at_utc": T0,
        "core_version": "github.com/dimaggi-ai/tool-guard-core@v0.3.0", "request_id": "request-1", "binding_digest": digest,
        "actual_binding": copy.deepcopy(binding), "boundary_reasons": [], "core_envelope": {"synthetic_test": True},
        "core_policy": {"decision": "denied" if states[0] == "not_sent" else "allowed"}, "effective_expiry_utc": expiry,
        "permission": "not_granted", "attempt": "not_started", "workload_outcome": "not_observed", "dispatch_possible": False,
    }
    records = []
    for index, state in enumerate(states):
        prior = None if index == 0 else states[index - 1]
        reason = {
            (None, "prepared"): "synthetic_policy_allowed", (None, "not_sent"): "policy_not_allowed",
            ("prepared", "dispatching"): "synthetic_transport_entered", ("prepared", "not_sent"): "pre_send_policy_not_allowed",
            ("prepared", "unknown"): "process_recovery", ("dispatching", "unknown"): "synthetic_lost_response",
            ("dispatching", "acknowledged"): "synthetic_acknowledgement", ("unknown", "unknown"): "synthetic_absence_inconclusive",
            ("unknown", "acknowledged"): "synthetic_matching_object",
        }[(prior, state)]
        snapshot = {
            "request_id": "request-1", "binding_digest": digest, "operation_digest": operation,
            "operation_id": "synthetic-operation:" + operation[7:], "attempt_id": "synthetic-attempt:" + operation[7:],
            "revision": index + 1, "state": state, "reason": reason, "created_at_utc": T0,
            "updated_at_utc": f"2026-09-19T12:00:0{index}Z", "evidence_class": "synthetic", "permission": "not_granted",
            "dispatch_possible": False, "workload_outcome": "not_observed", "input": copy.deepcopy(supplied),
            "report_bytes_base64": base64.b64encode(raw).decode(), "preview": copy.deepcopy(preview), "observation": None,
        }
        if state == "acknowledged" or index == 3:
            snapshot["observation"] = {
                "kind": "matching_object" if state == "acknowledged" else "absent",
                "operation_id": snapshot["operation_id"], "attempt_id": snapshot["attempt_id"], "binding_digest": digest,
                "target_scope": binding["target_scope"], "object_uid": "synthetic-object:" + operation[7:] if state == "acknowledged" else "",
                "observed_at_utc": snapshot["updated_at_utc"],
            }
        records.append({"source_sequence": index + 2, "record_digest": sha(encoded(snapshot).encode()), "snapshot": snapshot})
    return {"schema": "dimaggi-batch-journal-export/v1", "journal_id": JOURNAL, "epoch": "1", "evidence_class": "synthetic",
            "permission": "not_granted", "dispatch_possible": False, "records": records}


def intent():
    binding = export()["records"][0]["snapshot"]["input"]["report_binding"]
    return {"request_id": "request-1", "report_id": binding["report_id"], "profile_id": binding["profile_id"],
            "target_id": binding["target_scope"], "workload_id": binding["selected_request"], "desired_state": "succeeded",
            "sources": {"permission": JOURNAL + "/permission", "attempt": JOURNAL + "/attempt", "workload": "synthetic-workload-observer"},
            "evidence_class": "synthetic"}


def setup(store):
    planned = intent()
    for kind, source in planned["sources"].items():
        store.register_source(source, kind, planned["target_id"])
    store.register_intent(planned)


@pytest.fixture
def store(tmp_path):
    with ObservationStore(tmp_path / "import.sqlite") as result:
        setup(result)
        yield result


def ingest(store, value=None, **kwargs):
    return import_journal(store, encoded(export() if value is None else value), request_id="request-1",
                          expected_journal_id=JOURNAL, recorded_at_utc=kwargs.get("recorded_at_utc", T9))


def projection(store, **kwargs):
    return store.project("request-1", as_of_utc=kwargs.get("as_of_utc", T9), freshness_seconds=kwargs.get("freshness_seconds", FRESHNESS))


def test_import_retains_all_attempt_states_without_permission_or_outcome(store):
    result = ingest(store)
    assert result["imported_events"] == 6
    assert result["duplicate_events"] == 0
    assert result["permission"] == "not_granted" and result["execution_proven"] is False
    history = store.history("request-1")
    assert [item["event"]["state"] for item in history] == ["unresolved", "not_started", "unknown", "unknown", "unknown", "submitted"]
    assert history[-1]["event"]["payload"]["source_record"] == export()["records"][-1]
    assert history[-1]["event"]["object_id"] == "synthetic-object:" + sha(b"operation")[7:]
    result = projection(store)
    assert result["execution_proven"] is False and result["mutation_request"] is None
    assert result["projections"]["workload"]["status"] == "missing"
    assert {case["reason"] for case in result["cases"]} == {"permission_unresolved", "workload_missing"}


def test_restart_duplicate_import_retains_original_collection_and_observation_times(tmp_path):
    path = tmp_path / "import.sqlite"
    with ObservationStore(path) as first:
        setup(first)
        ingest(first)
        original = first.history("request-1")
    with ObservationStore(path) as second:
        repeated = ingest(second, recorded_at_utc="2026-09-19T12:02:00Z")
        assert repeated["imported_events"] == 0 and repeated["duplicate_events"] == 6
        assert second.history("request-1") == original
        result = projection(second, as_of_utc="2026-09-19T12:02:00Z")
        assert result["projections"]["attempt"]["status"] == "stale"
        assert result["projections"]["permission"]["status"] == "stale"


def test_absence_preserves_unknown_and_does_not_fabricate_workload_observation(store):
    value = export(("prepared", "dispatching", "unknown", "unknown"))
    ingest(store, value)
    result = projection(store)
    assert "attempt_unknown" in {case["reason"] for case in result["cases"]}
    assert result["projections"]["workload"]["status"] == "missing"
    assert result["recommendation"] == "escalate" and result["automatic_resubmission"] is False
    snapshot = store.history("request-1")[-1]["event"]["payload"]["source_record"]["snapshot"]
    assert snapshot["observation"]["kind"] == "absent"


@pytest.mark.parametrize("states", [("not_sent",), ("prepared", "not_sent"), ("prepared", "unknown")])
def test_refusal_expiry_and_recovery_are_supported_without_claiming_execution(store, states):
    ingest(store, export(states))
    latest = projection(store)["projections"]["attempt"]["latest_records"][0]["event"]
    assert latest["state"] == ("unknown" if states[-1] == "unknown" else "not_started")
    assert latest["object_id"] is None


@pytest.mark.parametrize("path,value", [
    (("schema",), "other/v1"), (("journal_id",), "foreign"), (("epoch",), "2"),
    (("evidence_class",), "observed"), (("permission",), "allowed"), (("dispatch_possible",), 0),
    (("records", 0, "source_sequence"), True), (("records", 0, "source_sequence"), 2.0),
    (("records", 0, "source_sequence"), 3), (("records", 1, "source_sequence"), 2),
    (("records", 0, "record_digest"), "SHA256:" + "a" * 64),
    (("records", 0, "snapshot", "revision"), True), (("records", 0, "snapshot", "revision"), 1.0),
    (("records", 1, "snapshot", "revision"), 3),
    (("records", 0, "snapshot", "state"), "succeeded"),
    (("records", 0, "snapshot", "permission"), "allowed"),
    (("records", 0, "snapshot", "workload_outcome"), "succeeded"),
    (("records", 0, "snapshot", "evidence_class"), "observed"),
    (("records", 0, "snapshot", "operation_id"), "different-operation"),
    (("records", 0, "snapshot", "attempt_id"), "different-attempt"),
    (("records", 0, "snapshot", "report_bytes_base64"), "not base64!"),
    (("records", 0, "snapshot", "updated_at_utc"), "2026-09-19T12:00:00.0000001Z"),
    (("records", 0, "snapshot", "updated_at_utc"), "2026-09-18T12:00:00Z"),
    (("records", 0, "snapshot", "input", "request_id"), "other-request"),
    (("records", 0, "snapshot", "input", "approval", "status"), []),
    (("records", 0, "snapshot", "input", "report_binding", "cardinality"), True),
    (("records", 0, "snapshot", "input", "report_binding", "target_scope"), "https://real-cluster"),
    (("records", 0, "snapshot", "preview", "permission"), "allowed"),
    (("records", 0, "snapshot", "preview", "dispatch_possible"), True),
    (("records", 0, "snapshot", "preview", "contract"), "model"),
    (("records", 0, "snapshot", "preview", "binding_digest"), sha(b"changed")),
    (("records", 0, "snapshot", "preview", "actual_binding", "cardinality"), True),
    (("records", 0, "snapshot", "preview", "actual_binding", "cardinality"), 1.0),
    (("records", 0, "snapshot", "preview", "boundary_reasons"), [False]),
    (("records", 0, "snapshot", "preview", "core_policy", "decision"), "allow"),
    (("records", 0, "snapshot", "preview", "core_policy", "decision"), "denied"),
    (("records", 0, "snapshot", "preview", "effective_expiry_utc"), None),
    (("records", 0, "snapshot", "preview", "effective_expiry_utc"), T0),
    (("records", 0, "snapshot", "input", "approval", "status"), "denied"),
    (("records", 1, "snapshot", "input", "approval", "expires_at_utc"), "2026-09-19T13:00:00Z"),
    (("records", 4, "snapshot", "observation", "object_uid"), "different-object"),
    (("records", 4, "snapshot", "observation", "attempt_id"), "different-attempt"),
    (("records", 4, "snapshot", "observation", "kind"), "absent"),
    (("records", 4, "snapshot", "observation", "target_scope"), "synthetic://different"),
    (("records", 4, "snapshot", "observation", "observed_at_utc"), "2026-09-19T12:00:05Z"),
])
def test_changed_or_invalid_contract_refuses_before_first_append(store, path, value):
    candidate = export()
    node = candidate
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ObservationError):
        ingest(store, candidate)
    assert store.history("request-1") == []


@pytest.mark.parametrize("path", [(), ("records", 0), ("records", 0, "snapshot"),
    ("records", 0, "snapshot", "input"), ("records", 0, "snapshot", "input", "report_binding"),
    ("records", 0, "snapshot", "input", "approval"), ("records", 0, "snapshot", "preview"),
    ("records", 4, "snapshot", "observation")])
@pytest.mark.parametrize("mode", ["unknown", "missing"])
def test_adapter_fields_require_exact_shape(store, path, mode):
    candidate = export()
    node = candidate
    for key in path:
        node = node[key]
    if mode == "unknown":
        node["unexpected"] = None
    else:
        del node[next(iter(node))]
    with pytest.raises(ObservationError):
        ingest(store, candidate)
    assert store.history("request-1") == []


@pytest.mark.parametrize("text", [
    '{"schema":"x","schema":"y"}', '{"nested":{"id":"a","id":"b"}}',
    '{"x":NaN}', '{"x":1e999}', '{"x":"\\ud800"}', '[] {}',
])
def test_transport_duplicates_nonfinite_and_unicode_refuse(store, text):
    with pytest.raises(ObservationError):
        import_journal(store, text, request_id="request-1", expected_journal_id=JOURNAL, recorded_at_utc=T9)
    assert store.history("request-1") == []


def test_transport_size_is_explicitly_bounded(store):
    with pytest.raises(ObservationError, match="invalid journal JSON"):
        import_journal(store, " " * (4 * 1024 * 1024 + 1), request_id="request-1", expected_journal_id=JOURNAL, recorded_at_utc=T9)
    assert store.history("request-1") == []


@pytest.mark.parametrize("field,value", [
    ("report_id", "changed"), ("profile_id", "changed"), ("target_id", "changed"),
    ("workload_id", "changed"), ("evidence_class", "observed"),
])
def test_registered_intent_is_the_binding_anchor(field, value):
    planned = intent()
    planned[field] = value
    with pytest.raises(ObservationError):
        journal_events(encoded(export()), intent=planned, expected_journal_id=JOURNAL)


def test_wrong_configured_source_refuses():
    planned = intent()
    planned["sources"]["attempt"] = "other-source"
    with pytest.raises(ObservationError, match="configured source"):
        journal_events(encoded(export()), intent=planned, expected_journal_id=JOURNAL)


def test_import_time_is_checked_for_every_record_before_append(store):
    with pytest.raises(ObservationError, match="import time"):
        ingest(store, recorded_at_utc=T0)
    assert store.history("request-1") == []


def test_missing_history_or_repeated_source_record_refuses(store):
    for records in ([], export()["records"][1:], [export()["records"][0]] * 2):
        candidate = export()
        candidate["records"] = records
        with pytest.raises(ObservationError):
            ingest(store, candidate)
        assert store.history("request-1") == []


def test_source_digest_is_preserved_opaque_but_changed_reuse_becomes_durable_conflict(store):
    original = export(("prepared",))
    ingest(store, original)
    changed = copy.deepcopy(original)
    changed["records"][0]["record_digest"] = sha(b"different-source-record")
    with pytest.raises(SourceConflict):
        ingest(store, changed)
    assert len(store.history("request-1")) == 2
    assert "source_conflict" in {case["reason"] for case in projection(store)["cases"]}


def test_report_byte_change_with_unchanged_digest_refuses(store):
    candidate = export()
    candidate["records"][0]["snapshot"]["report_bytes_base64"] = base64.b64encode(b"{}").decode()
    with pytest.raises(ObservationError, match="bound digest"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_older_export_prefix_cannot_remove_unknown_history(store):
    ingest(store, export(("prepared", "dispatching", "unknown")))
    repeated = ingest(store, export(("prepared",)))
    assert repeated["imported_events"] == 0 and repeated["duplicate_events"] == 2
    assert len(store.history("request-1")) == 4
    assert "attempt_unknown" in {case["reason"] for case in projection(store)["cases"]}


def test_separately_observed_absence_cannot_close_imported_unknown(store):
    ingest(store, export(("prepared", "dispatching", "unknown")))
    planned = intent()
    store.append({
        **{key: planned[key] for key in ("request_id", "report_id", "profile_id", "target_id", "workload_id")},
        "event_id": "independent-absence", "kind": "workload", "source_id": planned["sources"]["workload"],
        "source_epoch": "1", "source_record_id": planned["workload_id"], "source_version": "opaque-observation-v1",
        "source_sequence": 1, "observed_at_utc": T9, "object_id": None, "state": "absent", "evidence_class": "synthetic", "payload": {},
    }, recorded_at_utc=T9)
    result = projection(store)
    assert {"attempt_unknown", "desired_observed_divergence"} <= {case["reason"] for case in result["cases"]}
    assert result["recommendation"] == "escalate" and result["mutation_request"] is None


def test_not_sent_with_recorded_allowed_decision_refuses(store):
    candidate = export(("not_sent",))
    candidate["records"][0]["snapshot"]["preview"]["core_policy"]["decision"] = "allowed"
    with pytest.raises(ObservationError, match="initial attempt state"):
        ingest(store, candidate)


@pytest.mark.parametrize("states,time", [
    (("prepared", "dispatching"), "2026-09-19T12:01:00Z"),
    (("prepared", "not_sent"), T0),
])
def test_pre_send_transition_must_respect_exact_expiry(store, states, time):
    candidate = export(states)
    candidate["records"][1]["snapshot"]["updated_at_utc"] = time
    with pytest.raises(ObservationError, match="pre-send transition"):
        ingest(store, candidate, recorded_at_utc="2026-09-19T12:02:00Z")
    assert store.history("request-1") == []


def test_same_attempt_identity_cannot_be_rebound_inside_full_export(store):
    candidate = export(("prepared",))
    second = copy.deepcopy(candidate["records"][0])
    second["source_sequence"] = 3
    second["record_digest"] = sha(b"second-request")
    snapshot = second["snapshot"]
    snapshot["request_id"] = snapshot["input"]["request_id"] = snapshot["preview"]["request_id"] = "request-2"
    candidate["records"].append(second)
    with pytest.raises(ObservationError, match="different requests"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_stale_matching_observation_before_previous_transition_refuses(store):
    candidate = export()
    candidate["records"][-1]["snapshot"]["observation"]["observed_at_utc"] = T0
    with pytest.raises(ObservationError, match="predates"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_allowed_source_requires_expected_binding(store):
    candidate = export()
    for record in candidate["records"]:
        record["snapshot"]["input"]["expected_binding"] = None
    with pytest.raises(ObservationError, match="approval or expiry"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_freeform_reason_cannot_disguise_incompatible_source_transition(store):
    candidate = export()
    candidate["records"][2]["snapshot"]["reason"] = "definitely_not_sent"
    with pytest.raises(ObservationError, match="source reason"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_frozen_opaque_core_values_cannot_change_json_scalar_types(store):
    candidate = export()
    for record in candidate["records"]:
        record["snapshot"]["preview"]["core_policy"]["rules_evaluated"] = 1
    candidate["records"][-1]["snapshot"]["preview"]["core_policy"]["rules_evaluated"] = 1.0
    with pytest.raises(ObservationError, match="immutable"):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_source_clock_cannot_regress_across_separate_requests(store):
    candidate = export(("prepared", "dispatching"))
    second = copy.deepcopy(candidate["records"][0])
    second["source_sequence"] = 4
    second["record_digest"] = sha(b"second-request")
    # Its individually consistent reservation time precedes the prior source
    # record. Global clock validation must run before cross-request attribution.
    second["snapshot"]["request_id"] = "request-2"
    second["snapshot"]["input"]["request_id"] = "request-2"
    second["snapshot"]["preview"]["request_id"] = "request-2"
    candidate["records"].append(second)
    with pytest.raises(ObservationError, match="source times regress"):
        ingest(store, candidate)
    assert store.history("request-1") == []


@pytest.mark.parametrize("readiness", ["invalid", "refused", "incomplete", "unknown-future-state"])
def test_coherently_changed_readiness_cannot_retain_allowed_verdict(store, readiness):
    candidate = export()
    for record in candidate["records"]:
        snapshot = record["snapshot"]
        report = json.loads(base64.b64decode(snapshot["report_bytes_base64"]))
        report["execution_readiness"] = readiness
        raw = encoded(report).encode()
        snapshot["report_bytes_base64"] = base64.b64encode(raw).decode()
        for binding in (snapshot["input"]["report_binding"], snapshot["input"]["expected_binding"], snapshot["preview"]["actual_binding"]):
            binding["execution_readiness"] = readiness
            binding["report_digest"] = sha(raw)
    with pytest.raises(ObservationError):
        ingest(store, candidate)
    assert store.history("request-1") == []


def test_missing_selected_request_refuses_without_creating_intent(store):
    planned = intent()
    planned["request_id"] = "absent-request"
    with pytest.raises(ObservationError, match="absent"):
        journal_events(encoded(export()), intent=planned, expected_journal_id=JOURNAL)
    assert store.history("request-1") == []


@pytest.mark.parametrize("value", [None, {}, 12, bytearray(b"{}")])
def test_direct_api_non_json_values_refuse(value):
    with pytest.raises(ObservationError, match="JSON text"):
        journal_events(value, intent=intent(), expected_journal_id=JOURNAL)
