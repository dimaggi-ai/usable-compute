"""Independent synthetic Job/Pod attribution and outcome challenges."""
import copy
import json

import pytest

from dimaggi_receiver.kubernetes_import import kubernetes_event, import_kubernetes
from dimaggi_receiver.observations import ObservationError, ObservationStore


AT = "2026-09-19T12:00:00Z"
COLLECTOR = "synthetic-kubernetes-collector"
IDENTITY = {"cluster_id": "synthetic-cluster", "namespace_name": "lab", "namespace_uid": "namespace-uid-0", "job_name": "batch-0", "job_uid": "job-uid-0"}
INTENT = {"request_id": "synthetic-request", "report_id": "synthetic-report", "profile_id": "synthetic-profile", "target_id": "synthetic://lab/scope", "workload_id": "batch-0", "desired_state": "succeeded", "evidence_class": "synthetic", "sources": {"permission": "synthetic-permission", "attempt": "synthetic-attempt", "workload": COLLECTOR}}


def adversarial_bundle():
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "batch-pod-0", "namespace": "lab", "uid": "pod-uid-0", "resourceVersion": "pod-rv-token", "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": "batch-0", "uid": "job-uid-0", "controller": True}]}, "status": {"phase": "Succeeded"}}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "batch-0", "namespace": "lab", "uid": "job-uid-0", "resourceVersion": "job-rv-token"}, "spec": {"parallelism": 1, "completions": 1}, "status": {"conditions": [{"type": "Complete", "status": "True"}], "succeeded": 1}}
    return {"schema": "dimaggi-kubernetes-observation/v1", "evidence_class": "synthetic", "collector_id": COLLECTOR, "source_epoch": "epoch-1", "source_sequence": 1, "observed_at_utc": AT, **{key: INTENT[key] for key in ("request_id", "report_id", "profile_id", "target_id", "workload_id")}, "identity": copy.deepcopy(IDENTITY), "job": job, "pods": [pod], "pods_complete": True, "absence": None}


def event(bundle):
    return kubernetes_event(json.dumps(bundle), intent=copy.deepcopy(INTENT), expected_collector_id=COLLECTOR, expected_identity=copy.deepcopy(IDENTITY))


def test_independent_valid_terminal_job_and_opaque_versions():
    value = adversarial_bundle()
    result = event(value)
    assert result["state"] == "succeeded"
    assert result["evidence_class"] == "synthetic"
    # Changing opaque tokens cannot supply a client-invented ordering rule.
    value["job"]["metadata"]["resourceVersion"] = "000-not-a-number"
    value["pods"][0]["metadata"]["resourceVersion"] = "999999999999999999999999999999999999999"
    assert event(value)["state"] == "succeeded"


@pytest.mark.parametrize("change", ["foreign_job_uid", "foreign_pod_namespace", "foreign_controller_uid", "noncontroller_owner", "two_controllers", "duplicate_pod_uid", "boolean_sequence"])
def test_independent_attribution_conflicts_are_refused(change):
    value = adversarial_bundle()
    pod = value["pods"][0]
    if change == "foreign_job_uid": value["job"]["metadata"]["uid"] = "replacement-job-uid"
    elif change == "foreign_pod_namespace": pod["metadata"]["namespace"] = "foreign"
    elif change == "foreign_controller_uid": pod["metadata"]["ownerReferences"][0]["uid"] = "foreign-job-uid"
    elif change == "noncontroller_owner": pod["metadata"]["ownerReferences"][0]["controller"] = False
    elif change == "two_controllers": pod["metadata"]["ownerReferences"].append({"apiVersion": "batch/v1", "kind": "Job", "name": "foreign-job", "uid": "foreign-job-uid", "controller": True})
    elif change == "duplicate_pod_uid": value["pods"].append(copy.deepcopy(pod))
    else: value["source_sequence"] = True
    with pytest.raises(ObservationError): event(value)


@pytest.mark.parametrize("condition", ["SuccessCriteriaMet", "FailureTarget"])
def test_independent_early_conditions_are_not_terminal(condition):
    value = adversarial_bundle()
    value["job"]["status"] = {"conditions": [{"type": condition, "status": "True"}]}
    assert event(value)["state"] not in {"succeeded", "failed"}


def test_independent_failed_pod_is_not_failed_job_and_all_retries_retained():
    value = adversarial_bundle()
    value["job"]["status"] = {"conditions": [], "active": 1, "failed": 1}
    value["pods"][0]["status"]["phase"] = "Failed"
    retry = copy.deepcopy(value["pods"][0])
    retry["metadata"]["name"] = "batch-pod-retry"
    retry["metadata"]["uid"] = "pod-uid-retry"
    retry["status"]["phase"] = "Running"
    value["pods"].append(retry)
    result = event(value)
    assert result["state"] not in {"succeeded", "failed"}
    retained = json.dumps(result["payload"])
    assert "pod-uid-0" in retained and "pod-uid-retry" in retained


@pytest.mark.parametrize("change", ["incomplete_pods", "job_deleting", "no_job_no_absence"])
def test_independent_incomplete_or_deleting_evidence_does_not_claim_success(change):
    value = adversarial_bundle()
    if change == "incomplete_pods": value["pods_complete"] = False
    elif change == "job_deleting": value["job"]["metadata"]["deletionTimestamp"] = AT
    else: value["job"] = None
    assert event(value)["state"] not in {"succeeded", "failed"}


@pytest.mark.parametrize("conditions", [
    ["Complete", "Failed"], ["Failed", "SuccessCriteriaMet"],
    ["FailureTarget", "SuccessCriteriaMet"],
])
def test_independent_contradictory_conditions_remain_unknown(conditions):
    value = adversarial_bundle()
    value["job"]["status"]["conditions"] = [{"type": condition, "status": "True"} for condition in conditions]
    result = event(value)
    assert result["state"] == "unknown"
    assert "contradictory_job_conditions" in result["payload"]["interpretation_reasons"]


def test_independent_terminal_job_with_ready_pods_is_not_quiescent():
    value = adversarial_bundle()
    value["job"]["status"]["ready"] = 1
    assert event(value)["state"] == "unknown"


def configured_memory_store():
    store = ObservationStore(":memory:")
    for kind, source in INTENT["sources"].items():
        store.register_source(source, kind, INTENT["target_id"])
    store.register_intent(copy.deepcopy(INTENT))
    return store


def import_bundle(store, bundle):
    return import_kubernetes(store, json.dumps(bundle), request_id=INTENT["request_id"], expected_collector_id=COLLECTOR, expected_identity=copy.deepcopy(IDENTITY), recorded_at_utc="2026-09-19T12:00:09Z")


@pytest.mark.parametrize("resource", ["Job", "Pod"])
def test_independent_later_collector_sequence_cannot_erase_terminal_object(resource):
    with configured_memory_store() as store:
        original = adversarial_bundle()
        original["source_sequence"] = 3
        import_bundle(store, original)
        changed = copy.deepcopy(original)
        changed["source_sequence"] = 4
        changed["observed_at_utc"] = "2026-09-19T12:00:01Z"
        if resource == "Job":
            changed["job"]["status"]["conditions"] = []
            changed["job"]["metadata"]["resourceVersion"] = "another-job-token"
        else:
            changed["pods"][0]["status"]["phase"] = "Running"
            changed["pods"][0]["metadata"]["resourceVersion"] = "another-pod-token"
        with pytest.raises(ObservationError): import_bundle(store, changed)
        assert len(store.history(INTENT["request_id"])) == 1


def test_independent_late_older_collection_does_not_replace_terminal_projection():
    with configured_memory_store() as store:
        original = adversarial_bundle()
        original["source_sequence"] = 3
        import_bundle(store, original)
        earlier = copy.deepcopy(original)
        earlier["source_sequence"] = 1
        earlier["observed_at_utc"] = "2026-09-19T11:59:59Z"
        earlier["job"]["status"]["conditions"] = []
        earlier["job"]["metadata"]["resourceVersion"] = "older-opaque-job-token"
        import_bundle(store, earlier)
        projected = store.project(INTENT["request_id"], as_of_utc="2026-09-19T12:00:09Z", freshness_seconds={"permission":60,"attempt":60,"workload":60})
        latest = projected["projections"]["workload"]["latest_records"]
        assert len(latest) == 1 and latest[0]["event"]["state"] == "succeeded"
        assert latest[0]["event"]["source_sequence"] == 3
        assert len(store.history(INTENT["request_id"])) == 2
