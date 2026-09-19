"""Import TENWA's closed synthetic journal as attributed application evidence.

This module neither evaluates policy nor changes executor authority. The source
record digest is opaque: Go's serialization is not Python's canonical encoding.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

from .jsonio import canonical, digest, loads
from .observations import IDENTITIES, ObservationError, ObservationStore, _utc


EXPORT_KEYS = {"schema", "journal_id", "epoch", "evidence_class", "permission", "dispatch_possible", "records"}
RECORD_KEYS = {"source_sequence", "record_digest", "snapshot"}
SNAPSHOT_KEYS = {
    "request_id", "binding_digest", "operation_digest", "operation_id", "attempt_id", "revision",
    "state", "reason", "created_at_utc", "updated_at_utc", "evidence_class", "permission",
    "dispatch_possible", "workload_outcome", "input", "report_bytes_base64", "preview", "observation",
}
INPUT_KEYS = {"schema", "request_id", "report_binding", "expected_binding", "approval", "evidence_expires_at_utc"}
BINDING_KEYS = {
    "report_id", "report_digest", "profile_id", "profile_digest", "evidence_class", "evidence_digest",
    "selected_request", "execution_readiness", "action_class", "cardinality", "target_scope", "payload_digest",
}
PREVIEW_KEYS = {
    "schema", "contract", "evaluated_at_utc", "core_version", "request_id", "binding_digest", "actual_binding",
    "boundary_reasons", "core_envelope", "core_policy", "effective_expiry_utc", "permission", "attempt",
    "workload_outcome", "dispatch_possible",
}
OBSERVATION_KEYS = {"kind", "operation_id", "attempt_id", "binding_digest", "target_scope", "object_uid", "observed_at_utc"}
REPORT_KEYS = {
    "schema_version", "report_id", "profile_id", "profile_digest", "evidence_class", "evidence_digest",
    "selected_request", "execution_readiness", "action_intent", "mutation_request",
}
STATE_MAP = {"prepared": "not_started", "not_sent": "not_started", "dispatching": "unknown", "unknown": "unknown", "acknowledged": "submitted"}
TRANSITIONS = {
    "prepared": {"dispatching", "unknown", "not_sent"},
    "dispatching": {"unknown", "acknowledged"},
    "unknown": {"unknown", "acknowledged"}, "acknowledged": set(), "not_sent": set(),
}
TRANSITION_REASONS = {
    (None, "prepared"): {"synthetic_policy_allowed"}, (None, "not_sent"): {"policy_not_allowed"},
    ("prepared", "dispatching"): {"synthetic_transport_entered"},
    ("prepared", "not_sent"): {"pre_send_policy_not_allowed"},
    ("prepared", "unknown"): {"process_recovery"},
    ("dispatching", "unknown"): {"process_recovery", "synthetic_lost_response", "synthetic_transport_error"},
    ("dispatching", "acknowledged"): {"synthetic_acknowledgement"},
    ("unknown", "unknown"): {"synthetic_absence_inconclusive"},
    ("unknown", "acknowledged"): {"synthetic_matching_object"},
}
IMMUTABLE = SNAPSHOT_KEYS - {"revision", "state", "reason", "updated_at_utc", "observation"}


def _shape(value: Any, keys: set[str], name: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise ObservationError(f"{name} fields do not match the journal export contract")


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ObservationError(f"{name} must be a nonempty trimmed string")
    return value


def _sha(value: Any, name: str) -> None:
    if type(value) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ObservationError(f"{name} must be a lowercase SHA-256 identifier")


def _nonexecuting(value: dict[str, Any]) -> None:
    if value["permission"] != "not_granted" or value["dispatch_possible"] is not False:
        raise ObservationError("journal export cannot grant permission or enable dispatch")


def _binding(value: Any) -> None:
    _shape(value, BINDING_KEYS, "binding")
    for key in BINDING_KEYS - {"cardinality"}:
        _text(value[key], key)
    for key in ("report_digest", "profile_digest", "evidence_digest", "payload_digest"):
        _sha(value[key], key)
    if (type(value["cardinality"]) is not int or value["cardinality"] != 1
            or value["action_class"] != "submit_bounded_batch"
            or value["evidence_class"] != "synthetic_contract_fixture"
            or value["execution_readiness"] not in {"feasible", "refused", "incomplete", "invalid"}
            or not value["target_scope"].startswith("synthetic://")):
        raise ObservationError("only the closed synthetic one-batch binding is supported")


def _validate_snapshot(snapshot: Any) -> None:
    _shape(snapshot, SNAPSHOT_KEYS, "snapshot")
    for key in ("request_id", "operation_id", "attempt_id", "reason"):
        _text(snapshot[key], key)
    for key in ("binding_digest", "operation_digest"):
        _sha(snapshot[key], key)
    if (snapshot["operation_id"] != "synthetic-operation:" + snapshot["operation_digest"][7:]
            or snapshot["attempt_id"] != "synthetic-attempt:" + snapshot["operation_digest"][7:]):
        raise ObservationError("synthetic operation and attempt identities do not match their binding")
    if type(snapshot["revision"]) is not int or not 1 <= snapshot["revision"] <= 2**63 - 1:
        raise ObservationError("snapshot revision must be a positive bounded integer")
    if type(snapshot["state"]) is not str or snapshot["state"] not in STATE_MAP:
        raise ObservationError("unsupported attempt state")
    if snapshot["evidence_class"] != "synthetic" or snapshot["workload_outcome"] != "not_observed":
        raise ObservationError("journal snapshots establish only synthetic attempt evidence")
    _nonexecuting(snapshot)
    if _utc(snapshot["created_at_utc"]) > _utc(snapshot["updated_at_utc"]):
        raise ObservationError("snapshot transition precedes reservation")

    supplied = snapshot["input"]
    _shape(supplied, INPUT_KEYS, "input")
    if supplied["schema"] != "dimaggi-batch-preview/v1" or supplied["request_id"] != snapshot["request_id"]:
        raise ObservationError("input request does not match the snapshot")
    binding = supplied["report_binding"]
    _binding(binding)
    if supplied["expected_binding"] is not None:
        _binding(supplied["expected_binding"])
        if supplied["expected_binding"] != binding:
            raise ObservationError("expected and report bindings differ")
    approval = supplied["approval"]
    _shape(approval, {"status", "binding_digest", "expires_at_utc"}, "approval")
    if type(approval["status"]) is not str or approval["status"] not in {"not_requested", "synthetic_granted", "denied"}:
        raise ObservationError("unsupported synthetic approval state")
    if approval["binding_digest"] is not None:
        _sha(approval["binding_digest"], "approval binding digest")
    for timestamp in (approval["expires_at_utc"], supplied["evidence_expires_at_utc"]):
        if timestamp is not None:
            _utc(timestamp)

    preview = snapshot["preview"]
    _shape(preview, PREVIEW_KEYS, "preview")
    _binding(preview["actual_binding"])
    _nonexecuting(preview)
    if (preview["schema"] != "dimaggi-batch-preview-result/v1" or preview["contract"] != "synthetic-lab-test"
            or preview["core_version"] != "github.com/dimaggi-ai/tool-guard-core@v0.3.0"
            or preview["attempt"] != "not_started" or preview["workload_outcome"] != "not_observed"
            or preview["request_id"] != snapshot["request_id"]
            or preview["binding_digest"] != snapshot["binding_digest"]
            or preview["actual_binding"] != binding
            or _utc(preview["evaluated_at_utc"]) != _utc(snapshot["created_at_utc"])):
        raise ObservationError("preview does not bind the original synthetic reservation")
    if type(preview["boundary_reasons"]) is not list or any(type(item) is not str for item in preview["boundary_reasons"]):
        raise ObservationError("preview boundary reasons must be strings")
    # Core owns these schemas and policy meanings. Preserve their finite JSON;
    # check only the recorded verdict's consistency with this source protocol.
    if type(preview["core_envelope"]) is not dict or type(preview["core_policy"]) is not dict:
        raise ObservationError("opaque Core records must be JSON objects")
    verdict = preview["core_policy"].get("decision")
    if type(verdict) is not str or verdict not in {"allowed", "denied", "escalated", "flagged"}:
        raise ObservationError("unsupported recorded Core decision")
    if preview["effective_expiry_utc"] is not None:
        _utc(preview["effective_expiry_utc"])
    if verdict == "allowed":
        expiries = (approval["expires_at_utc"], supplied["evidence_expires_at_utc"])
        if (binding["execution_readiness"] != "feasible" or supplied["expected_binding"] is None
                or any(item is None for item in expiries) or preview["effective_expiry_utc"] is None
                or _utc(preview["effective_expiry_utc"]) != min(_utc(item) for item in expiries)
                or _utc(snapshot["created_at_utc"]) >= _utc(preview["effective_expiry_utc"])
                or approval["status"] != "synthetic_granted" or approval["binding_digest"] != snapshot["binding_digest"]
                or preview["boundary_reasons"]):
            raise ObservationError("recorded allowed preview contradicts its approval or expiry")

    try:
        report_bytes = base64.b64decode(snapshot["report_bytes_base64"], validate=True)
        report = loads(report_bytes.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError, binascii.Error) as exc:
        raise ObservationError("report must be strict base64-encoded UTF-8 JSON") from exc
    if "sha256:" + hashlib.sha256(report_bytes).hexdigest() != binding["report_digest"]:
        raise ObservationError("report bytes do not match the bound digest")
    _shape(report, REPORT_KEYS, "synthetic report")
    if report["schema_version"] != "dimaggi-receiver-report/v1" or report["mutation_request"] is not None:
        raise ObservationError("unsupported synthetic report")
    for key in ("report_id", "profile_id", "profile_digest", "evidence_class", "evidence_digest", "selected_request", "execution_readiness"):
        if report[key] != binding[key]:
            raise ObservationError("report fields differ from the evaluated binding")
    action = report["action_intent"]
    _shape(action, {"action_class", "cardinality", "designated_scheduler_scope", "workload_artifact_digest"}, "report action")
    if (type(action["cardinality"]) is not int or action["cardinality"] != 1
            or action["action_class"] != binding["action_class"]
            or action["designated_scheduler_scope"] != binding["target_scope"]
            or action["workload_artifact_digest"] != binding["payload_digest"]):
        raise ObservationError("report action differs from the bound intent")

    observation = snapshot["observation"]
    if observation is None:
        if snapshot["state"] == "acknowledged":
            raise ObservationError("acknowledgement requires attributed object evidence")
        return
    _shape(observation, OBSERVATION_KEYS, "observation")
    if snapshot["state"] not in {"unknown", "acknowledged"}:
        raise ObservationError("object observation is inconsistent with the attempt state")
    for key in ("operation_id", "attempt_id", "binding_digest"):
        if observation[key] != snapshot[key]:
            raise ObservationError("observation is attributed to a different attempt")
    if observation["target_scope"] != binding["target_scope"]:
        raise ObservationError("observation target differs from the request")
    if not _utc(snapshot["created_at_utc"]) <= _utc(observation["observed_at_utc"]) <= _utc(snapshot["updated_at_utc"]):
        raise ObservationError("observation time falls outside the attempt history")
    if observation["kind"] == "matching_object" and snapshot["state"] == "acknowledged":
        _text(observation["object_uid"], "object UID")
        if observation["object_uid"] != "synthetic-object:" + snapshot["operation_digest"][7:]:
            raise ObservationError("synthetic object identity differs from the bound operation")
    elif observation["kind"] == "absent" and snapshot["state"] == "unknown" and observation["object_uid"] == "":
        pass
    else:
        raise ObservationError("object absence cannot establish acknowledgement or no effects")


def journal_events(export_json: str | bytes, *, intent: dict[str, Any], expected_journal_id: str) -> list[dict[str, Any]]:
    """Validate a full export, then derive evidence for one preconfigured intent.

    Uses the existing strict 4 MiB JSON boundary. Unknown outer/nested adapter
    fields refuse; Core's two opaque records are retained without reinterpretation.
    No record digest or imported source is an authentication proof.
    """
    if type(export_json) not in (str, bytes):
        raise ObservationError("journal export must be JSON text or UTF-8 bytes")
    try:
        export = loads(export_json.decode("utf-8") if isinstance(export_json, bytes) else export_json)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ObservationError("invalid journal JSON") from exc
    _shape(export, EXPORT_KEYS, "export")
    _text(expected_journal_id, "expected journal ID")
    if (export["schema"] != "dimaggi-batch-journal-export/v1" or export["journal_id"] != expected_journal_id
            or export["epoch"] != "1" or export["evidence_class"] != "synthetic"):
        raise ObservationError("unsupported or incorrectly attributed synthetic journal")
    _nonexecuting(export)
    if intent["evidence_class"] != "synthetic":
        raise ObservationError("synthetic journal cannot populate an observed intent")
    for kind in ("permission", "attempt"):
        if intent["sources"][kind] != expected_journal_id + "/" + kind:
            raise ObservationError("journal is not the configured source for this intent")
    records = export["records"]
    if type(records) is not list or not 1 <= len(records) <= 10000:
        raise ObservationError("journal export requires bounded complete history")
    previous: dict[str, dict[str, Any]] = {}
    attempt_owners: dict[str, str] = {}
    seen_digests: set[str] = set()
    events = []
    last_source_time = None
    for expected_sequence, record in enumerate(records, 2):
        _shape(record, RECORD_KEYS, "record")
        if type(record["source_sequence"]) is not int or record["source_sequence"] != expected_sequence:
            raise ObservationError("full journal source sequences must start at 2 and remain contiguous")
        _sha(record["record_digest"], "record digest")
        if record["record_digest"] in seen_digests:
            raise ObservationError("duplicate immutable source record")
        seen_digests.add(record["record_digest"])
        snapshot = record["snapshot"]
        _validate_snapshot(snapshot)
        source_time = _utc(snapshot["updated_at_utc"])
        if last_source_time is not None and source_time < last_source_time:
            raise ObservationError("journal source times regress across record sequence")
        last_source_time = source_time
        owner = attempt_owners.setdefault(snapshot["attempt_id"], snapshot["request_id"])
        if owner != snapshot["request_id"]:
            raise ObservationError("one source attempt cannot belong to different requests")
        prior = previous.get(snapshot["request_id"])
        if snapshot["reason"] not in TRANSITION_REASONS.get((None if prior is None else prior["state"], snapshot["state"]), set()):
            raise ObservationError("source reason contradicts the recorded transition")
        if prior is None:
            if snapshot["revision"] != 1 or snapshot["state"] not in {"prepared", "not_sent"}:
                raise ObservationError("request history must begin at its reservation")
            allowed = snapshot["preview"]["core_policy"]["decision"] == "allowed"
            if (allowed != (snapshot["state"] == "prepared")
                    or _utc(snapshot["created_at_utc"]) != _utc(snapshot["updated_at_utc"])):
                raise ObservationError("initial attempt state contradicts its policy evaluation")
        elif (snapshot["revision"] != prior["revision"] + 1
              or snapshot["state"] not in TRANSITIONS[prior["state"]]
              or _utc(snapshot["updated_at_utc"]) < _utc(prior["updated_at_utc"])
              or any(canonical(snapshot[key]) != canonical(prior[key]) for key in IMMUTABLE)):
            raise ObservationError("request history changes immutable binding or attempt order")
        if prior is not None and prior["state"] == "prepared" and snapshot["state"] in {"dispatching", "not_sent"}:
            before_expiry = _utc(snapshot["updated_at_utc"]) < _utc(snapshot["preview"]["effective_expiry_utc"])
            if before_expiry != (snapshot["state"] == "dispatching"):
                raise ObservationError("pre-send transition contradicts the fixed approval expiry")
        observation = snapshot["observation"]
        if (prior is None or prior["state"] == "prepared" or snapshot["reason"] in {"process_recovery", "synthetic_lost_response", "synthetic_transport_error"}) and observation is not None:
            raise ObservationError("this transition cannot include an object observation")
        if prior is not None and prior["state"] == "unknown" and observation is None:
            raise ObservationError("unknown reconciliation requires explicit source observation")
        if observation is not None:
            if prior is not None and _utc(observation["observed_at_utc"]) < _utc(prior["updated_at_utc"]):
                raise ObservationError("reconciliation observation predates the prior attempt transition")
            if snapshot["reason"] == "synthetic_acknowledgement" and _utc(observation["observed_at_utc"]) != _utc(snapshot["updated_at_utc"]):
                raise ObservationError("simulation acknowledgement must reference its own transition time")
        previous[snapshot["request_id"]] = snapshot
        if snapshot["request_id"] != intent["request_id"]:
            continue
        binding = snapshot["input"]["report_binding"]
        expected = {"request_id": snapshot["request_id"], "report_id": binding["report_id"],
                    "profile_id": binding["profile_id"], "target_id": binding["target_scope"],
                    "workload_id": binding["selected_request"]}
        if any(intent[key] != expected[key] for key in IDENTITIES):
            raise ObservationError("journal request binding differs from registered application intent")
        for kind in (("permission", "attempt") if prior is None else ("attempt",)):
            observation = snapshot["observation"]
            object_id = observation["object_uid"] if kind == "attempt" and snapshot["state"] == "acknowledged" else None
            events.append({
                **expected, "event_id": digest([expected_journal_id, export["epoch"], kind, record["record_digest"]]),
                "kind": kind, "source_id": intent["sources"][kind], "source_epoch": export["epoch"],
                "source_record_id": snapshot["request_id"] if kind == "permission" else snapshot["attempt_id"],
                "source_version": record["record_digest"], "source_sequence": record["source_sequence"],
                "observed_at_utc": snapshot["updated_at_utc"], "object_id": object_id,
                "state": "unresolved" if kind == "permission" else STATE_MAP[snapshot["state"]],
                "evidence_class": "synthetic",
                "payload": {"journal_id": expected_journal_id, "source_state": snapshot["permission"] if kind == "permission" else snapshot["state"],
                            "source_record": record, "grants_permission": False, "dispatch_possible": False},
            })
    if not events:
        raise ObservationError("requested intent is absent from the journal export")
    return events


def import_journal(store: ObservationStore, export_json: str | bytes, *, request_id: str,
                   expected_journal_id: str, recorded_at_utc: str) -> dict[str, Any]:
    """Append validated synthetic read evidence; never create authority or intent.

    Validation finishes before the first append. Each append is independently
    durable; an I/O failure or existing-store conflict can leave an accepted
    prefix. Re-import is idempotent and does not refresh observation timestamps.
    """
    events = journal_events(export_json, intent=store.intent(request_id), expected_journal_id=expected_journal_id)
    recorded = _utc(recorded_at_utc)
    if any(_utc(event["observed_at_utc"]) > recorded for event in events):
        raise ObservationError("import time precedes source evidence")
    imported = sum(store.append(event, recorded_at_utc=recorded_at_utc) for event in events)
    return {"schema": "dimaggi-journal-import/v1", "journal_id": expected_journal_id, "request_id": request_id,
            "imported_events": imported, "duplicate_events": len(events) - imported,
            "evidence_class": "synthetic", "permission": "not_granted", "dispatch_possible": False,
            "execution_proven": False, "workload_outcome": "not_observed"}
