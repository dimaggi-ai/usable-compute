"""Closed synthetic Kubernetes Job/Pod observations; no API client or actions."""
from __future__ import annotations

from typing import Any

from .jsonio import canonical, digest, loads
from .observations import IDENTITIES, ObservationError, ObservationStore, _utc


SCHEMA = "dimaggi-kubernetes-observation/v1"
IDENTITY_KEYS = {"cluster_id", "namespace_name", "namespace_uid", "job_name", "job_uid"}
BUNDLE_KEYS = {
    "schema", "evidence_class", "collector_id", "source_epoch", "source_sequence", "observed_at_utc",
    *IDENTITIES, "identity", "job", "pods", "pods_complete", "absence",
}
PHASES = {"Pending", "Running", "Succeeded", "Failed", "Unknown"}
CONDITIONS = {"Complete", "Failed", "FailureTarget", "SuccessCriteriaMet", "Suspended"}


def _object(value: Any, name: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ObservationError(f"{name} must be a JSON object")
    return value


def _exact(value: Any, keys: set[str], name: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise ObservationError(f"{name} fields differ from the observation contract")


def _text(value: Any, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ObservationError(f"{name} must be a nonempty trimmed string")
    return value


def _integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ObservationError(f"{name} must be a nonnegative bounded integer")
    return value


def _metadata(resource: Any, *, kind: str, identity: dict[str, str], observed: Any) -> dict[str, Any]:
    _object(resource, kind)
    if resource.get("kind") != kind or resource.get("apiVersion") != ("batch/v1" if kind == "Job" else "v1"):
        raise ObservationError("unsupported Kubernetes resource kind or API version")
    metadata = _object(resource.get("metadata"), "metadata")
    for key in ("name", "namespace", "uid", "resourceVersion"):
        _text(metadata.get(key), "metadata." + key)
    if metadata["namespace"] != identity["namespace_name"]:
        raise ObservationError("resource namespace differs from the expected identity")
    if kind == "Job" and (metadata["uid"] != identity["job_uid"] or metadata["name"] != identity["job_name"]):
        raise ObservationError("Job name or immutable UID differs from the expected identity")
    for field in ("creationTimestamp", "deletionTimestamp"):
        value = metadata.get(field)
        if value is not None and _utc(value) > observed:
            raise ObservationError("resource timestamp is later than the observation")
    if metadata.get("creationTimestamp") is not None and metadata.get("deletionTimestamp") is not None:
        if _utc(metadata["deletionTimestamp"]) < _utc(metadata["creationTimestamp"]):
            raise ObservationError("resource deletion precedes creation")
    return metadata


def _pod(pod: Any, *, identity: dict[str, str], observed: Any) -> dict[str, Any]:
    metadata = _metadata(pod, kind="Pod", identity=identity, observed=observed)
    if metadata["uid"] == identity["job_uid"]:
        raise ObservationError("Pod and Job cannot share an immutable UID")
    references = metadata.get("ownerReferences")
    if type(references) is not list:
        raise ObservationError("Pod requires explicit owner references")
    controllers = []
    for reference in references:
        _object(reference, "owner reference")
        for key in ("apiVersion", "kind", "name", "uid"):
            _text(reference.get(key), "owner reference " + key)
        if "controller" in reference and type(reference["controller"]) is not bool:
            raise ObservationError("owner controller must be a boolean")
        if "blockOwnerDeletion" in reference and type(reference["blockOwnerDeletion"]) is not bool:
            raise ObservationError("blockOwnerDeletion must be a boolean")
        if reference.get("controller") is True:
            controllers.append(reference)
    if (len(controllers) != 1 or controllers[0]["apiVersion"] != "batch/v1" or controllers[0]["kind"] != "Job"
            or controllers[0]["name"] != identity["job_name"] or controllers[0]["uid"] != identity["job_uid"]):
        raise ObservationError("Pod controller is not the expected immutable Job")
    status = _object(pod.get("status", {}), "Pod status")
    phase = status.get("phase", "Unknown")
    if type(phase) is not str or phase not in PHASES:
        raise ObservationError("unsupported Pod phase")
    contradictory_container = False
    # Preserve container attempts and diagnostics. Counts are validated, never
    # used as a proxy for workload completion or resource measurements.
    for field in ("containerStatuses", "initContainerStatuses", "ephemeralContainerStatuses"):
        if field not in status:
            continue
        if type(status[field]) is not list:
            raise ObservationError("container status must be an array")
        names = set()
        for container in status[field]:
            _object(container, "container status")
            name = _text(container.get("name"), "container name")
            if name in names:
                raise ObservationError("duplicate container status name")
            names.add(name)
            if "restartCount" in container:
                _integer(container["restartCount"], "restartCount")
            if "state" in container:
                state = _object(container["state"], "container state")
                if len(state) > 1 or any(key not in {"waiting", "running", "terminated"} for key in state):
                    raise ObservationError("container state must name at most one supported lifecycle state")
                for value in state.values():
                    _object(value, "container lifecycle state")
                terminated = state.get("terminated")
                if terminated is not None and (type(terminated.get("exitCode")) is not int or not -(2**31) <= terminated["exitCode"] < 2**31):
                    raise ObservationError("container termination requires an integer exit code")
                if phase == "Succeeded" and field == "containerStatuses" and state:
                    contradictory_container |= terminated is None or terminated["exitCode"] != 0
    return {"pod_uid": metadata["uid"], "pod_name": metadata["name"], "resource_version": metadata["resourceVersion"],
            "phase": phase, "deletion_in_progress": metadata.get("deletionTimestamp") is not None,
            "contradictory_container_state": contradictory_container}


def _job_state(job: Any, pods: list[dict[str, Any]], *, identity: dict[str, str], observed: Any,
               pods_complete: bool) -> tuple[str, list[str]]:
    metadata = _metadata(job, kind="Job", identity=identity, observed=observed)
    spec = _object(job.get("spec"), "Job spec")
    if any(_integer(spec.get(field), field) != 1 for field in ("parallelism", "completions")):
        raise ObservationError("only explicit single-parallelism single-completion Jobs are supported")
    status = _object(job.get("status", {}), "Job status")
    for field in ("active", "succeeded", "failed", "ready", "terminating"):
        if field in status:
            _integer(status[field], "Job status " + field)
    conditions = status.get("conditions", [])
    if type(conditions) is not list:
        raise ObservationError("Job conditions must be an array")
    by_type = {}
    for condition in conditions:
        _object(condition, "Job condition")
        label = _text(condition.get("type"), "condition type")
        value = condition.get("status")
        if type(value) is not str or value not in {"True", "False", "Unknown"} or label in by_type:
            raise ObservationError("condition status must be explicit and condition types unique")
        for field in ("lastProbeTime", "lastTransitionTime"):
            if condition.get(field) is not None and _utc(condition[field]) > observed:
                raise ObservationError("condition time is later than observation")
        by_type[label] = value
    true = {key for key, value in by_type.items() if value == "True"}
    if metadata.get("deletionTimestamp") is not None:
        return "unknown", ["job_deletion_in_progress"]
    if not pods_complete:
        return "unknown", ["pod_evidence_incomplete"]
    if any(pod["contradictory_container_state"] for pod in pods):
        return "unknown", ["pod_phase_contradicts_container_state"]
    if any(key not in CONDITIONS for key in by_type) or "Unknown" in by_type.values():
        return "unknown", ["unsupported_or_unknown_job_condition"]
    if ((true & {"Complete", "SuccessCriteriaMet"} and true & {"Failed", "FailureTarget"})
            or ("Complete" in true and "Suspended" in true)):
        return "unknown", ["contradictory_job_conditions"]
    phases = {pod["phase"] for pod in pods}
    terminal = true & {"Complete", "Failed"}
    if terminal and (phases & {"Pending", "Running", "Unknown"} or any(pod["deletion_in_progress"] for pod in pods)
                     or status.get("active", 0) > 0 or status.get("terminating", 0) > 0 or status.get("ready", 0) > 0):
        return "unknown", ["terminal_job_evidence_not_quiescent"]
    if "Complete" in true:
        if "Succeeded" not in phases or ("succeeded" in status and status["succeeded"] < 1):
            return "unknown", ["completion_lacks_successful_pod_evidence"]
        return "succeeded", ["job_complete_with_attributed_pod_evidence"]
    if "Failed" in true:
        return "failed", ["job_failed_terminal_condition"]
    if true & {"SuccessCriteriaMet", "FailureTarget"}:
        return "unknown", ["job_termination_pending"]
    if "Suspended" in true:
        return "pending", ["job_suspended"]
    if "Unknown" in phases or any(pod["deletion_in_progress"] for pod in pods):
        return "unknown", ["pod_state_unknown_or_terminating"]
    if "Running" in phases:
        return "running", ["attributed_pod_running"]
    if "Pending" in phases:
        return "pending", ["attributed_pod_pending"]
    if phases & {"Failed", "Succeeded"}:
        return "unknown", ["pod_terminal_state_is_not_job_completion"]
    return "pending", ["job_present_waiting_for_pod_evidence"]


def _kubernetes_event(bundle_json: str | bytes, *, intent: dict[str, Any], expected_collector_id: str,
                      expected_identity: dict[str, str], evidence_class: str) -> dict[str, Any]:
    """Validate one synthetic bundle and derive one workload read event.

    Outer fields are exact; unconsumed Kubernetes resource fields are retained as
    opaque finite JSON. Unknown is not zero, success, deletion or absence.
    """
    if type(bundle_json) not in (str, bytes):
        raise ObservationError("observation bundle must be JSON text or UTF-8 bytes")
    try:
        bundle = loads(bundle_json.decode("utf-8") if isinstance(bundle_json, bytes) else bundle_json)
    except (ValueError, UnicodeError) as exc:
        raise ObservationError("invalid Kubernetes observation JSON") from exc
    _exact(bundle, BUNDLE_KEYS, "observation bundle")
    _exact(expected_identity, IDENTITY_KEYS, "expected identity")
    _exact(bundle["identity"], IDENTITY_KEYS, "source identity")
    for key in IDENTITY_KEYS:
        _text(expected_identity[key], key)
    if canonical(bundle["identity"]) != canonical(expected_identity):
        raise ObservationError("cluster, namespace or Job identity does not match configured scope")
    _text(expected_collector_id, "expected collector ID")
    if (bundle["schema"] != SCHEMA or bundle["evidence_class"] != evidence_class or intent["evidence_class"] != evidence_class
            or bundle["collector_id"] != expected_collector_id or intent["sources"]["workload"] != expected_collector_id):
        raise ObservationError("only the configured observation source and evidence class are supported")
    if any(bundle[key] != intent[key] for key in IDENTITIES):
        raise ObservationError("observation is not bound to the registered application intent")
    _text(bundle["source_epoch"], "source epoch")
    if _integer(bundle["source_sequence"], "source sequence") < 1:
        raise ObservationError("source sequence must be a positive collector integer")
    observed = _utc(bundle["observed_at_utc"])
    if type(bundle["pods"]) is not list or len(bundle["pods"]) > 1000 or type(bundle["pods_complete"]) is not bool:
        raise ObservationError("pods must be a bounded array with explicit completeness")
    pods = [_pod(pod, identity=expected_identity, observed=observed) for pod in bundle["pods"]]
    if len({pod["pod_uid"] for pod in pods}) != len(pods) or len({pod["pod_name"] for pod in pods}) != len(pods):
        raise ObservationError("one snapshot cannot duplicate Pod UIDs or reuse a Pod name")
    absence = bundle["absence"]
    if absence is not None:
        _exact(absence, {"kind", "http_status", "observed_at_utc"}, "absence evidence")
        if (absence["kind"] != "job_get_not_found" or type(absence["http_status"]) is not int or absence["http_status"] != 404
                or _utc(absence["observed_at_utc"]) != observed or bundle["job"] is not None):
            raise ObservationError("absence requires an explicit scoped not-found assertion and no Job object")
    if bundle["job"] is None:
        state, reasons = ("absent", ["explicit_job_not_found"]) if absence is not None else ("unknown", ["job_evidence_missing"])
        object_id = None
    else:
        state, reasons = _job_state(bundle["job"], pods, identity=expected_identity, observed=observed,
                                    pods_complete=bundle["pods_complete"])
        object_id = expected_identity["job_uid"]
    version = digest(bundle)
    return {
        **{key: intent[key] for key in IDENTITIES}, "event_id": digest([SCHEMA, expected_collector_id, version]),
        "kind": "workload", "source_id": expected_collector_id, "source_epoch": bundle["source_epoch"],
        "source_record_id": intent["workload_id"], "source_version": version, "source_sequence": bundle["source_sequence"],
        "observed_at_utc": bundle["observed_at_utc"], "object_id": object_id, "state": state, "evidence_class": evidence_class,
        "payload": {"adapter_schema": SCHEMA, "identity": expected_identity, "source_bundle": bundle, "pod_attempts": pods,
                    "interpretation_reasons": reasons, "grants_permission": False, "dispatch_possible": False,
                    "execution_proven": False},
    }


def kubernetes_event(bundle_json: str | bytes, *, intent: dict[str, Any], expected_collector_id: str,
                     expected_identity: dict[str, str]) -> dict[str, Any]:
    """Public file import remains synthetic-only; JSON cannot assert TLS provenance."""
    return _kubernetes_event(bundle_json, intent=intent, expected_collector_id=expected_collector_id,
                             expected_identity=expected_identity, evidence_class="synthetic")


def import_kubernetes(store: ObservationStore, bundle_json: str | bytes, *, request_id: str,
                      expected_collector_id: str, expected_identity: dict[str, str], recorded_at_utc: str) -> dict[str, Any]:
    """Append one synthetic workload projection while preserving prior attempts."""
    event = kubernetes_event(bundle_json, intent=store.intent(request_id), expected_collector_id=expected_collector_id,
                             expected_identity=expected_identity)
    return _persist_event(store, event, expected_identity=expected_identity, recorded_at_utc=recorded_at_utc)


def _persist_event(store: ObservationStore, event: dict[str, Any], *, expected_identity: dict[str, str],
                   recorded_at_utc: str) -> dict[str, Any]:
    request_id = event["request_id"]
    if _utc(recorded_at_utc) < _utc(event["observed_at_utc"]):
        raise ObservationError("collection time precedes the source observation")
    previous_versions = {}
    previous_names = {}
    previous_terminal = {}
    for item in store.history(request_id):
        prior = item["event"]
        if prior["kind"] == "workload" and prior["payload"].get("adapter_schema") == SCHEMA:
            if canonical(prior["payload"]["identity"]) != canonical(expected_identity):
                store._conflict(event, recorded_at_utc, "a registered workload cannot change its physical scope or immutable Job UID")
            # Identical API object versions cannot acquire different contents.
            # Never order or numerically compare Kubernetes resource versions.
            old = prior["payload"]["source_bundle"]
            old_objects = ([old["job"]] if old["job"] is not None else []) + old["pods"]
            for previous in old_objects:
                metadata = previous["metadata"]
                previous_versions[(previous["kind"], metadata["uid"], metadata["resourceVersion"])] = canonical(previous)
                previous_names[(previous["kind"], metadata["uid"])] = metadata["name"]
                if prior["source_epoch"] == event["source_epoch"] and prior["source_sequence"] <= event["source_sequence"]:
                    if previous["kind"] == "Job":
                        terminal = {condition["type"] for condition in previous.get("status", {}).get("conditions", [])
                                    if condition["status"] == "True" and condition["type"] in {"Complete", "Failed", "FailureTarget"}}
                    else:
                        phase = previous.get("status", {}).get("phase")
                        terminal = {phase} if phase in {"Succeeded", "Failed"} else set()
                    previous_terminal.setdefault((previous["kind"], metadata["uid"]), set()).update(terminal)
    new = event["payload"]["source_bundle"]
    for current in ([new["job"]] if new["job"] is not None else []) + new["pods"]:
        metadata = current["metadata"]
        version = previous_versions.get((current["kind"], metadata["uid"], metadata["resourceVersion"]))
        if version is not None and version != canonical(current):
            store._conflict(event, recorded_at_utc, "same immutable object resourceVersion has contradictory contents")
        name = previous_names.get((current["kind"], metadata["uid"]))
        if name is not None and name != metadata["name"]:
            store._conflict(event, recorded_at_utc, "immutable object UID cannot change its name")
        if current["kind"] == "Job":
            terminal = {condition["type"] for condition in current.get("status", {}).get("conditions", []) if condition["status"] == "True"}
        else:
            terminal = {current.get("status", {}).get("phase")}
        if not previous_terminal.get((current["kind"], metadata["uid"]), set()) <= terminal:
            store._conflict(event, recorded_at_utc, "later collector evidence regresses immutable-object terminal state")
    imported = store.append(event, recorded_at_utc=recorded_at_utc)
    return {"schema": "dimaggi-kubernetes-import/v1", "request_id": request_id, "imported_events": int(imported),
            "duplicate_events": int(not imported), "state": event["state"], "evidence_class": event["evidence_class"],
            "permission": "not_granted", "dispatch_possible": False, "execution_proven": False}
