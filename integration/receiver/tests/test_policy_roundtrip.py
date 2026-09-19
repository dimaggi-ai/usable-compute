"""Actual installed Python report -> compiled TENWA/Core command contract.

Set DIMAGGI_TEST_SOURCES and DIMAGGI_BATCH_PREVIEW to run this integration suite.
No scheduler, credentials, mutable action or workload executor is involved.
"""
import copy
import json
import math
import os
from pathlib import Path
import subprocess

import pytest

from dimaggi_receiver.bindings import policy_input
from dimaggi_receiver.jsonio import dumps
from dimaggi_receiver.report import build_report


@pytest.fixture(scope="module")
def reports():
    bundle = os.environ.get("DIMAGGI_TEST_SOURCES")
    if not bundle:
        pytest.skip("explicit locked source bundle required")
    return {case: build_report(Path(bundle), case) for case in (
        "baseline", "wider_network", "restore_geometry", "restore_and_network",
        "insufficient_power", "smaller_gang", "no_change")}


def preview(tmp_path, raw, request):
    binary = os.environ.get("DIMAGGI_BATCH_PREVIEW")
    if not binary:
        pytest.skip("compiled TENWA batch-preview required")
    path = tmp_path / "report.json"
    path.write_bytes(raw)
    process = subprocess.run([binary, "--report", str(path), "--now", "2026-09-19T12:00:00Z"],
                             input=dumps(request), text=True, capture_output=True, timeout=15)
    assert process.returncode == 0, process.stdout + process.stderr
    return json.loads(process.stdout)


def test_known_model_answers_survive_real_receiver(reports):
    assert reports["baseline"]["engineering_readiness"] == "refused"
    raw = reports["baseline"]["evidence"]["raw"]["candidate"]
    assert raw["count_only_admission_gpus"] == 256
    assert raw["actual_allocated_gpus"] == 0
    assert reports["wider_network"]["comparison"]["cohort_delta_gpu_h"] == 0
    healthy = reports["restore_geometry"]
    assert healthy["engineering_readiness"] == "feasible_within_profile"
    assert healthy["evidence"]["raw"]["candidate"]["actual_allocated_gpus"] == 384
    assert math.isclose(healthy["accounting"]["buckets"]["useful_compute_gpu_h"],
                        210430.58465522234, rel_tol=1e-12)
    assert reports["insufficient_power"]["engineering_readiness"] == "refused"
    small = reports["smaller_gang"]
    assert small["engineering_readiness"] == "refused"
    assert small["comparison"]["state"] == "incomparable"
    assert small["comparison"]["cohort_delta_gpu_h"] is None
    assert small["evidence"]["raw"]["memory"]["rough_screen"] == "fail"
    assert reports["no_change"]["recommendation"] == "retain_current_state"
    assert reports["no_change"]["comparison"]["cohort_delta_gpu_h"] == 0


@pytest.mark.parametrize("case", ["baseline", "wider_network", "restore_geometry", "restore_and_network",
                                  "insufficient_power", "smaller_gang", "no_change"])
def test_model_report_cannot_become_permission(reports, tmp_path, case):
    report = reports[case]
    assert report["execution_readiness"] == "incomplete"
    assert report["mutation_request"] is None
    assert report["comparison"]["selected_request_gain_gpu_h"] is None
    raw = dumps(report).encode()
    request = policy_input(raw, "receiver-" + case)
    result = preview(tmp_path, raw, request)
    assert result["core_policy"]["decision"] == "denied"
    assert "model_evidence_cannot_authorize_execution" in result["boundary_reasons"]
    assert result["actual_binding"] == request["report_binding"]
    assert result["permission"] == "not_granted"
    assert result["attempt"] == "not_started"
    assert result["workload_outcome"] == "not_observed"
    assert result["dispatch_possible"] is False


def test_changed_report_bytes_invalidate_binding(reports, tmp_path):
    raw = dumps(reports["restore_geometry"]).encode()
    request = policy_input(raw, "changed-bytes")
    result = preview(tmp_path, raw + b"\n", request)
    assert "report_binding_changed" in result["boundary_reasons"]
    assert result["core_policy"]["decision"] == "denied"


def test_changed_reviewed_binding_is_visible(reports, tmp_path):
    raw = dumps(reports["restore_geometry"]).encode()
    request = policy_input(raw, "changed-review")
    request["expected_binding"] = copy.deepcopy(request["report_binding"])
    request["expected_binding"]["selected_request"] = "job-1"
    result = preview(tmp_path, raw, request)
    assert "reviewed_binding_changed" in result["boundary_reasons"]
    assert result["dispatch_possible"] is False
