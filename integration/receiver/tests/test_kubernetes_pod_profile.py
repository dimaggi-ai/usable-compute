"""Actual-resource fixture replay; transport is mocked, not a live test."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time

import pytest
from dimaggi_receiver.kubernetes_collect import Collector, POD_PROFILE
from dimaggi_receiver.observations import ObservationError
from test_kubernetes_collect import lab, tls_material, collect

FIXTURE = json.loads((Path(__file__).parent / "fixtures/kubernetes-v1.35.0-local-pod.json").read_text())


def test_absent_list_item_type_metadata_is_supplied_and_raw_hash_retained(lab):
    for item in lab[1]["pods"]["items"]:
        item.pop("apiVersion")
        item.pop("kind")
    raw = json.dumps(lab[1]["pods"]).encode()
    assert collect(lab)["state"] == "unknown"
    event = lab[3].history("request-1")[0]["event"]
    assert event["payload"]["source_bundle"]["pods"][0]["kind"] == "Pod"
    assert event["payload"]["collection"]["pod_list_raw_digest"] == "sha256:" + hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("field,value", [("kind",None),("kind","Job"),("kind",{}),("apiVersion",None),("apiVersion","v2")])
def test_explicit_wrong_list_type_refuses(lab, field, value):
    lab[1]["pods"]["items"][0][field] = value
    with pytest.raises(ObservationError): collect(lab)
    assert lab[3].history("request-1") == []


@pytest.mark.parametrize("change", [None,"no_opt_in","priority","bool_priority","preemption","class","extra","seconds","bool_seconds","duplicate","missing","reorder","effect","key","named_kind","named_absent_kind"])
def test_exact_actual_admission_profile_and_mutations(lab, monkeypatch, change):
    job = copy.deepcopy(FIXTURE["job"])
    pod = copy.deepcopy(FIXTURE["pod_list"]["items"][0])
    pod.update(apiVersion="v1", kind="Pod")
    config = replace(lab[0], namespace_name=job["metadata"]["namespace"], job_name=job["metadata"]["name"],
                     job_uid=job["metadata"]["uid"], pod_profile=POD_PROFILE if change != "no_opt_in" else "")
    if change == "priority": pod["spec"]["priority"] = 1
    elif change == "bool_priority": pod["spec"]["priority"] = False
    elif change == "preemption": pod["spec"]["preemptionPolicy"] = "Never"
    elif change == "class": pod["spec"]["priorityClassName"] = "arbitrary"
    elif change == "extra": pod["spec"]["unknownAdmission"] = True
    elif change == "seconds": pod["spec"]["tolerations"][0]["tolerationSeconds"] = 301
    elif change == "bool_seconds": pod["spec"]["tolerations"][0]["tolerationSeconds"] = True
    elif change == "duplicate": pod["spec"]["tolerations"].append(pod["spec"]["tolerations"][0])
    elif change == "missing": pod["spec"].pop("priority")
    elif change == "reorder": pod["spec"]["tolerations"].reverse()
    elif change == "effect": pod["spec"]["tolerations"][0]["effect"] = "NoSchedule"
    elif change == "key": pod["spec"]["tolerations"][0]["key"] = "foreign"
    collector = Collector(config)
    stdout = FIXTURE["stdout"].encode()
    calls = []
    def get(path, deadline, **kwargs):
        calls.append(path)
        if "/log?" in path: return None, stdout
        named = copy.deepcopy(pod)
        if change == "named_kind": named["kind"] = "Job"
        elif change == "named_absent_kind": named.pop("kind")
        return named, json.dumps(named).encode()
    monkeypatch.setattr(collector, "_get", get)
    args = (job,[pod],{"expected_stdout_digest":"sha256:"+hashlib.sha256(stdout).hexdigest(),"max_output_bytes":65536},"/api/v1/namespaces/dimaggi-lab",time.monotonic()+30)
    if change in ("named_kind","named_absent_kind"):
        with pytest.raises(ObservationError): collector._runtime(*args)
        assert len(calls)==1
    else:
        checks, verified = collector._runtime(*args)
        assert verified is (change is None)
        assert checks[0]["spec_verified"] is (change is None)
        assert len(calls)==(3 if change is None else 0)


def test_profile_is_explicit_and_part_of_source_identity(lab):
    strict = Collector(lab[0])
    profiled = Collector(replace(lab[0], pod_profile=POD_PROFILE))
    assert "pod_profile" not in strict._descriptor
    assert profiled._descriptor["pod_profile"] == POD_PROFILE
    with pytest.raises(ObservationError): Collector(replace(lab[0], pod_profile="arbitrary"))
