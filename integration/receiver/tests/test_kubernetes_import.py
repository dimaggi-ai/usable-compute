"""Synthetic resource observations, never Kubernetes execution evidence."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from dimaggi_receiver.kubernetes_import import import_kubernetes, kubernetes_event
from dimaggi_receiver.observations import ObservationError, ObservationStore


COLLECTOR = "synthetic-kubernetes-collector"
T0 = "2026-09-19T12:00:00Z"
T1 = "2026-09-19T12:00:01Z"
T2 = "2026-09-19T12:00:02Z"
T3 = "2026-09-19T12:00:03Z"
FRESHNESS = {kind: 60 for kind in ("permission", "attempt", "workload")}


def identity():
    return {"cluster_id": "synthetic-cluster-1", "namespace_name": "synthetic-lab", "namespace_uid": "namespace-uid-1",
            "job_name": "synthetic-job-0", "job_uid": "job-uid-1"}


def intent():
    return {"request_id": "request-1", "report_id": "synthetic-report", "profile_id": "synthetic-cpu/v1",
            "target_id": "synthetic://cpu-lab/not-a-cluster", "workload_id": "synthetic-job-0", "desired_state": "succeeded",
            "sources": {"permission": "synthetic-permission", "attempt": "synthetic-attempt", "workload": COLLECTOR},
            "evidence_class": "synthetic"}


def pod(uid="pod-uid-1", phase="Succeeded"):
    return {"apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": "synthetic-" + uid, "namespace": "synthetic-lab", "uid": uid,
                         "resourceVersion": "opaque-pod-version/alpha", "creationTimestamp": T0,
                         "ownerReferences": [{"apiVersion": "batch/v1", "kind": "Job", "name": "synthetic-job-0",
                                              "uid": "job-uid-1", "controller": True, "blockOwnerDeletion": True}]},
            "status": {"phase": phase, "containerStatuses": [{"name": "workload", "restartCount": 0,
                                                                   "state": {"terminated": {"exitCode": 0}}}]}}


def bundle():
    planned = intent()
    return {"schema": "dimaggi-kubernetes-observation/v1", "evidence_class": "synthetic", "collector_id": COLLECTOR,
            "source_epoch": "collector-epoch-1", "source_sequence": 1, "observed_at_utc": T1,
            **{key: planned[key] for key in ("request_id", "report_id", "profile_id", "target_id", "workload_id")},
            "identity": identity(),
            "job": {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "synthetic-job-0", "namespace": "synthetic-lab",
                    "uid": "job-uid-1", "resourceVersion": "opaque-job-version/zeta", "creationTimestamp": T0},
                    "spec": {"parallelism": 1, "completions": 1},
                    "status": {"active": 0, "succeeded": 1, "failed": 0, "conditions": [{"type": "Complete", "status": "True",
                                                                                             "lastTransitionTime": T1}]}},
            "pods": [pod()], "pods_complete": True, "absence": None}


def event(value=None, **kwargs):
    return kubernetes_event(json.dumps(bundle() if value is None else value), intent=kwargs.get("intent", intent()),
                            expected_collector_id=COLLECTOR, expected_identity=kwargs.get("identity", identity()))


@pytest.fixture
def store(tmp_path):
    with ObservationStore(tmp_path / "kubernetes.sqlite") as journal:
        for kind, source in intent()["sources"].items():
            journal.register_source(source, kind, intent()["target_id"])
        journal.register_intent(intent())
        yield journal


def ingest(store, value=None, **kwargs):
    return import_kubernetes(store, json.dumps(bundle() if value is None else value), request_id="request-1",
                             expected_collector_id=COLLECTOR, expected_identity=kwargs.get("identity", identity()),
                             recorded_at_utc=kwargs.get("recorded_at_utc", T2))


def test_complete_job_and_attributed_successful_pod_remain_synthetic(store):
    result = ingest(store)
    assert result["state"] == "succeeded"
    assert result["permission"] == "not_granted" and result["execution_proven"] is False
    imported = store.history("request-1")[0]["event"]
    assert imported["object_id"] == "job-uid-1"
    assert imported["payload"]["source_bundle"] == bundle()
    assert imported["payload"]["pod_attempts"][0]["pod_uid"] == "pod-uid-1"
    assert imported["payload"]["pod_attempts"][0]["resource_version"] == "opaque-pod-version/alpha"


def test_retry_retains_failed_pod_and_running_replacement():
    value = bundle()
    value["job"]["status"] = {"active": 1, "failed": 1, "conditions": []}
    value["pods"] = [pod("failed-pod", "Failed"), pod("retry-pod", "Running")]
    result = event(value)
    assert result["state"] == "running"
    assert {p["pod_uid"] for p in result["payload"]["pod_attempts"]} == {"failed-pod", "retry-pod"}


@pytest.mark.parametrize("phase", ["Succeeded", "Failed"])
def test_terminal_pod_does_not_determine_job_outcome(phase):
    value = bundle()
    value["job"]["status"]["conditions"] = []
    value["pods"][0]["status"]["phase"] = phase
    assert event(value)["state"] == "unknown"


@pytest.mark.parametrize("condition", ["FailureTarget", "SuccessCriteriaMet"])
def test_early_job_condition_is_not_completion(condition):
    value = bundle()
    value["job"]["status"]["conditions"] = [{"type": condition, "status": "True"}]
    assert event(value)["state"] == "unknown"


def test_job_terminal_failure_is_distinct_from_submission():
    value = bundle()
    value["job"]["status"] = {"failed": 2, "conditions": [{"type": "Failed", "status": "True"}]}
    value["pods"] = [pod("failed-1", "Failed"), pod("failed-2", "Failed")]
    assert event(value)["state"] == "failed"


@pytest.mark.parametrize("change", ["missing_job", "empty_pods", "incomplete", "active_pod", "zero_success", "deleting_job", "deleting_pod", "conflict", "unknown_condition"])
def test_incomplete_or_contradictory_terminal_evidence_holds(change):
    value = bundle()
    if change == "missing_job":
        value["job"] = None
    elif change == "empty_pods":
        value["pods"] = []
    elif change == "incomplete":
        value["pods_complete"] = False
    elif change == "active_pod":
        value["pods"][0]["status"]["phase"] = "Running"
    elif change == "zero_success":
        value["job"]["status"]["succeeded"] = 0
    elif change == "deleting_job":
        value["job"]["metadata"]["deletionTimestamp"] = T1
        value["job"]["metadata"]["finalizers"] = ["foregroundDeletion"]
    elif change == "deleting_pod":
        value["pods"][0]["metadata"]["deletionTimestamp"] = T1
    elif change == "conflict":
        value["job"]["status"]["conditions"].append({"type": "Failed", "status": "True"})
    elif change == "unknown_condition":
        value["job"]["status"]["conditions"].append({"type": "FutureCondition", "status": "True"})
    result = event(value)
    assert result["state"] == "unknown"
    assert result["payload"]["execution_proven"] is False


def test_absence_requires_explicit_scoped_not_found_assertion():
    value = bundle()
    value["job"] = None
    value["pods"] = []
    assert event(value)["state"] == "unknown"
    value["absence"] = {"kind": "job_get_not_found", "http_status": 404, "observed_at_utc": T1}
    absent = event(value)
    assert absent["state"] == "absent" and absent["object_id"] is None
    assert absent["payload"]["source_bundle"]["absence"] == value["absence"]


def test_job_absence_can_coexist_with_remaining_owned_pod_without_no_effect_claim():
    value = bundle()
    value["job"] = None
    value["absence"] = {"kind": "job_get_not_found", "http_status": 404, "observed_at_utc": T1}
    result = event(value)
    assert result["state"] == "absent" and len(result["payload"]["pod_attempts"]) == 1
    assert result["payload"]["dispatch_possible"] is False


@pytest.mark.parametrize("path,bad", [
    (("schema",), "unknown/v1"), (("evidence_class",), "observed"), (("collector_id",), "unconfigured"),
    (("source_sequence",), True), (("source_sequence",), 1.0), (("source_sequence",), 0), (("source_sequence",), -1),
    (("source_epoch",), " "), (("pods_complete",), 1), (("pods",), {}),
    (("request_id",), "other-request"), (("target_id",), "other-scope"), (("profile_id",), "other-profile"),
    (("identity", "namespace_uid"), "different-namespace"), (("identity", "cluster_id"), "different-cluster"),
    (("job", "metadata", "uid"), "foreign-job"), (("job", "metadata", "name"), "foreign-name"),
    (("job", "metadata", "namespace"), "foreign-namespace"), (("job", "metadata", "resourceVersion"), 100),
    (("job", "kind"), "CronJob"), (("job", "apiVersion"), "batch/v2"),
    (("job", "spec", "parallelism"), True), (("job", "spec", "completions"), 2),
    (("job", "status", "succeeded"), True), (("job", "status", "failed"), -1),
    (("job", "status", "conditions", 0, "status"), True),
    (("job", "status", "conditions", 0, "lastTransitionTime"), T3),
    (("pods", 0, "metadata", "uid"), "job-uid-1"),
    (("pods", 0, "metadata", "namespace"), "foreign-namespace"),
    (("pods", 0, "metadata", "ownerReferences", 0, "uid"), "foreign-job"),
    (("pods", 0, "metadata", "ownerReferences", 0, "controller"), 1),
    (("pods", 0, "metadata", "ownerReferences", 0, "controller"), False),
    (("pods", 0, "status", "phase"), "CrashLoopBackOff"),
    (("pods", 0, "status", "containerStatuses", 0, "restartCount"), True),
    (("observed_at_utc",), "2026-09-19T12:00:01.0000001Z"),
])
def test_malformed_or_foreign_boundaries_refuse_without_import(store, path, bad):
    value = bundle()
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = bad
    with pytest.raises(ObservationError):
        ingest(store, value)
    assert store.history("request-1") == []


@pytest.mark.parametrize("scope", ["bundle", "identity", "absence"])
@pytest.mark.parametrize("mode", ["missing", "unknown"])
def test_wrapper_schemas_are_exact(scope, mode):
    value = bundle()
    value["job"] = None
    value["absence"] = {"kind": "job_get_not_found", "http_status": 404, "observed_at_utc": T1}
    target = value if scope == "bundle" else value[scope]
    if mode == "missing":
        del target[next(iter(target))]
    else:
        target["unexpected"] = None
    with pytest.raises(ObservationError):
        event(value)


def test_opaque_resource_fields_are_preserved_without_claiming_their_meaning():
    value = bundle()
    value["job"]["metadata"]["labels"] = {"application": "synthetic"}
    value["job"]["spec"]["template"] = {"spec": {"resources": {"unknown": "uninterpreted"}}}
    result = event(value)
    assert result["payload"]["source_bundle"] == value


def test_duplicate_import_preserves_first_collection_and_does_not_refresh_freshness(store):
    ingest(store)
    original = store.history("request-1")
    repeated = ingest(store, recorded_at_utc="2026-09-19T12:02:00Z")
    assert repeated["duplicate_events"] == 1 and repeated["imported_events"] == 0
    assert store.history("request-1") == original
    result = store.project("request-1", as_of_utc="2026-09-19T12:02:00Z", freshness_seconds=FRESHNESS)
    assert result["projections"]["workload"]["status"] == "stale"


def test_later_bundle_keeps_previous_pod_attempts_and_does_not_order_resource_versions(store):
    first = bundle()
    first["job"]["status"] = {"conditions": [], "failed": 1}
    first["pods"] = [pod("failed-pod", "Failed")]
    ingest(store, first)
    later = bundle()
    later["source_sequence"] = 2
    later["observed_at_utc"] = T2
    later["job"]["metadata"]["resourceVersion"] = "alpha-is-not-ordered-before-zeta"
    later["pods"] = [pod("replacement-pod", "Succeeded")]
    ingest(store, later)
    history = store.history("request-1")
    assert len(history) == 2
    assert history[0]["event"]["payload"]["pod_attempts"][0]["pod_uid"] == "failed-pod"
    assert history[1]["event"]["payload"]["pod_attempts"][0]["pod_uid"] == "replacement-pod"


def test_changed_physical_identity_cannot_be_approved_by_changing_import_argument(store):
    ingest(store)
    changed = bundle()
    changed["identity"]["namespace_uid"] = "recreated-namespace"
    changed["source_sequence"] = 2
    with pytest.raises(ObservationError, match="physical scope"):
        ingest(store, changed, identity=changed["identity"])
    assert len(store.history("request-1")) == 1


def test_same_object_resource_version_cannot_change_contents(store):
    ingest(store)
    changed = bundle()
    changed["source_sequence"] = 2
    changed["pods"][0]["status"]["phase"] = "Failed"
    with pytest.raises(ObservationError, match="resourceVersion"):
        ingest(store, changed)
    assert len(store.history("request-1")) == 1


def test_same_pod_uid_cannot_change_name_even_with_new_resource_version(store):
    ingest(store)
    changed = bundle()
    changed["source_sequence"] = 2
    changed["pods"][0]["metadata"].update(name="renamed", resourceVersion="next")
    with pytest.raises(ObservationError, match="change its name"):
        ingest(store, changed)
    assert len(store.history("request-1")) == 1


def test_collection_time_cannot_precede_observation(store):
    with pytest.raises(ObservationError, match="collection time"):
        ingest(store, recorded_at_utc=T0)


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '{"x":"\\udfff"}', "{} {}"])
def test_strict_transport_rejects_duplicates_nonfinite_unicode_and_trailing_data(text):
    with pytest.raises(ObservationError):
        kubernetes_event(text, intent=intent(), expected_collector_id=COLLECTOR, expected_identity=identity())


@pytest.mark.parametrize("state", [{"running": {}}, {"waiting": {}}, {"terminated": {"exitCode": 1}}])
def test_successful_pod_with_contradictory_current_container_state_holds(state):
    value = bundle()
    value["pods"][0]["status"]["containerStatuses"][0]["state"] = state
    result = event(value)
    assert result["state"] == "unknown"
    assert result["payload"]["interpretation_reasons"] == ["pod_phase_contradicts_container_state"]


def test_installed_cli_kubernetes_import_uses_separate_process_without_source_path(tmp_path):
    """Run only against a newly installed wheel; no source-tree fallback."""
    executable = Path(sys.executable).parent / "dimaggi-receiver"
    assert executable.is_file(), "install the receiver wheel into the test interpreter's environment"
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    paths = {}
    for name, value in (("intent", intent()), ("identity", identity()), ("bundle", bundle()), ("freshness", FRESHNESS)):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    journal = tmp_path / "installed.sqlite"

    def command(*arguments):
        completed = subprocess.run([str(executable), *map(str, arguments)], cwd=tmp_path, env=env,
                                   capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    initialized = command("observations-init", "--journal", journal, "--intent", paths["intent"])
    assert initialized["execution_authorized"] is False
    arguments = ("kubernetes-import", "--journal", journal, "--request-id", "request-1", "--input", paths["bundle"],
                 "--expected-collector-id", COLLECTOR, "--expected-identity", paths["identity"], "--recorded-at", T2)
    imported = command(*arguments)
    duplicate = command(*arguments)
    assert imported["imported_events"] == 1 and duplicate["duplicate_events"] == 1
    assert imported["permission"] == "not_granted" and imported["dispatch_possible"] is False
    projection = command("observations-show", "--journal", journal, "--request-id", "request-1",
                         "--as-of", T3, "--freshness", paths["freshness"])
    assert projection["projections"]["workload"]["latest_records"][0]["event"]["state"] == "succeeded"
    assert projection["execution_proven"] is False and projection["mutation_request"] is None
    assert projection["projections"]["permission"]["status"] == "missing"
