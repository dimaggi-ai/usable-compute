"""Required local cross-language acceptance against the actual TENWA verifier.

Hosted public CI excludes this file because the verifier belongs to the private
TENWA repository. The versioned handoff runs it with explicit source/binary pins.
"""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from dimaggi_receiver.kubernetes_collect import Collector, POD_PROFILE
from dimaggi_receiver.observations import ObservationError
from test_kubernetes_collect import lab, tls_material, NOW


@pytest.fixture
def actual(lab):
    binary = os.environ.get("DIMAGGI_BATCH_OBJECT_CHECK")
    fixtures = os.environ.get("DIMAGGI_BATCH_OBJECT_FIXTURES")
    payload = os.environ.get("DIMAGGI_BATCH_CPU_PAYLOAD")
    if not binary or not fixtures or not payload:
        pytest.skip("explicit TENWA object verifier, payload and source fixtures required")
    directory = Path(fixtures)
    plan_raw = (directory / "synthetic-intent.json").read_bytes()
    report_raw = (directory / "synthetic-report.json").read_bytes()
    plan, report = json.loads(plan_raw), json.loads(report_raw)
    job = json.loads((directory / "upstream-job.json").read_bytes())
    metadata = job["metadata"]
    job["status"] = {"active": 0, "succeeded": 1, "conditions": [{"type": "Complete", "status": "True"}]}
    config, resources, calls, store, _ = lab
    config = replace(config, cluster_id=plan["cluster_id"], namespace_name=plan["namespace"], namespace_uid=plan["namespace_uid"],
                     job_name=metadata["name"], job_uid=metadata["uid"], verifier_path=binary,
                     verifier_digest="sha256:" + hashlib.sha256(Path(binary).read_bytes()).hexdigest())
    registered = {"request_id": plan["request_id"], "report_id": report["report_id"], "profile_id": plan["profile"],
        "target_id": config.target_id, "workload_id": config.job_name, "desired_state": "succeeded",
        "evidence_class": "observed", "sources": store.intent("request-1")["sources"]}
    store.register_intent(registered)
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "synthetic-payload-pod", "namespace": config.namespace_name,
        "uid": "synthetic-pod-uid", "resourceVersion": "pod-version-1", "ownerReferences": [{"apiVersion": "batch/v1",
        "kind": "Job", "name": config.job_name, "uid": config.job_uid, "controller": True}]},
        "spec": copy.deepcopy(job["spec"]["template"]["spec"]), "status": {"phase": "Succeeded", "containerStatuses": [
            {"name": "payload", "restartCount": 0, "state": {"terminated": {"exitCode": 0}}}]}}
    pod["spec"]["nodeName"] = "synthetic-node"
    resources.update(job=job, pod=pod, log=subprocess.check_output([payload, "--request-id", plan["request_id"]], timeout=10),
        namespace={"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": config.namespace_name, "uid": config.namespace_uid}},
        pods={"apiVersion": "v1", "kind": "PodList", "metadata": {"resourceVersion": "list-1"}, "items": [pod]})
    return config, resources, calls, store, {"request_id": plan["request_id"], "intent_json": plan_raw, "report_json": report_raw}


def run(actual):
    config, _, _, store, args = actual
    return Collector(config, clock=lambda: NOW).collect(store, **args)


def test_real_matcher_tls_output_and_application_projection(actual):
    result = run(actual)
    assert result["state"] == "succeeded" and not result["execution_proven"] and not result["dispatch_possible"]
    event = actual[3].history(actual[4]["request_id"])[0]["event"]
    assert event["payload"]["collection"]["output_verified"] is True
    assert event["payload"]["collection"]["pod_spec_verified"] is True
    assert len(actual[2]) == 8


@pytest.mark.parametrize("change", ["job_image", "job_quantity", "job_identity", "pod_image", "pod_extra_field", "pod_null_node", "pod_null_status", "pod_null_metadata", "pod_null_state", "pod_null_terminated", "pod_null_container", "pod_restart", "wrong_stdout", "large_stdout"])
def test_changed_workload_and_output_cannot_establish_success(actual, change):
    resource = actual[1]
    if change == "job_image": resource["job"]["spec"]["template"]["spec"]["containers"][0]["image"] = "foreign"
    elif change == "job_quantity": resource["job"]["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"]["cpu"] = "0.1001"
    elif change == "job_identity": resource["job"]["metadata"]["uid"] = "foreign"
    elif change == "pod_image": resource["pod"]["spec"]["containers"][0]["image"] = "foreign"
    elif change == "pod_extra_field": resource["pod"]["spec"]["initContainers"] = []
    elif change == "pod_null_node": resource["pod"]["spec"]["nodeName"] = None
    elif change == "pod_null_status": resource["pod"]["status"] = None
    elif change == "pod_null_metadata": resource["pod"]["metadata"] = None
    elif change == "pod_null_state": resource["pod"]["status"]["containerStatuses"][0]["state"] = None
    elif change == "pod_null_terminated": resource["pod"]["status"]["containerStatuses"][0]["state"] = {"terminated": None}
    elif change == "pod_null_container": resource["pod"]["status"]["containerStatuses"] = [None]
    elif change == "pod_restart": resource["pod"]["status"]["containerStatuses"][0]["restartCount"] = 1
    elif change == "wrong_stdout": resource["log"] = b"forged expected text\n"
    elif change == "large_stdout": resource["log"] = b"x" * 257
    try:
        result = run(actual)
    except ObservationError:
        assert actual[3].history(actual[4]["request_id"]) == []
    else:
        assert result["state"] == "unknown" and not result["execution_proven"]


def test_scoped_job_not_found_remains_absence_not_no_effect(actual):
    actual[1]["job_http_status"] = 404
    result = run(actual)
    assert result["state"] == "absent" and result["permission"] == "not_granted"
    assert not result["execution_proven"] and not result["dispatch_possible"]
    assert len(actual[2]) == 5
    event = actual[3].history(actual[4]["request_id"])[0]["event"]
    assert event["payload"]["source_bundle"]["absence"]["http_status"] == 404
    assert event["payload"]["pod_attempts"]  # owned Pods may outlive the Job


def test_real_verifier_list_normalization_stock_defaults_and_profile_epoch(actual):
    config, resources, calls, store, kwargs = actual
    config = replace(config, pod_profile=POD_PROFILE)
    resources["pod"]["spec"].update(
        priority=0, preemptionPolicy="PreemptLowerPriority", tolerations=[
            {"key": "node.kubernetes.io/not-ready", "operator": "Exists",
             "effect": "NoExecute", "tolerationSeconds": 300},
            {"key": "node.kubernetes.io/unreachable", "operator": "Exists",
             "effect": "NoExecute", "tolerationSeconds": 300},
        ])
    # Kubernetes list items omit TypeMeta; named Pod reads retain it. Keep
    # independent representations so this exercises normalization end to end.
    listed = copy.deepcopy(resources["pod"])
    listed.pop("apiVersion")
    listed.pop("kind")
    resources["pods"]["items"] = [listed]
    result = Collector(config, clock=lambda: NOW).collect(store, **kwargs)
    assert result["state"] == "succeeded" and not result["execution_proven"]
    assert len(calls) == 8
    recorded = store.history(kwargs["request_id"])[0]["event"]["payload"]["collection"]
    assert recorded["pod_spec_verified"] and recorded["output_verified"]
    assert recorded["pod_comparison_profile"] == POD_PROFILE
    before = len(calls)
    with pytest.raises(ObservationError):
        Collector(replace(config, pod_profile=""), clock=lambda: NOW).collect(store, **kwargs)
    assert len(calls) == before and len(store.history(kwargs["request_id"])) == 1
