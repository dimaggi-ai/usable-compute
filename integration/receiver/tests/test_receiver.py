"""Actual model/domain adapter acceptance with an exported locked source bundle.

Set DIMAGGI_TEST_SOURCES to the exporter output and install the receiver first.
No tests infer a CPU executor, scheduler permission or operator performance.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from dimaggi_receiver.adapters import evaluate, exercise
from dimaggi_receiver.bindings import policy_input
from dimaggi_receiver.jsonio import digest, dumps, loads
from dimaggi_receiver.report import _assemble, build_report, trusted_profile, validate_report
from dimaggi_receiver.sources import verify_bundle


@pytest.fixture(scope="module")
def bundle():
    value = os.environ.get("DIMAGGI_TEST_SOURCES")
    if not value:
        pytest.skip("export reviewed sources and set DIMAGGI_TEST_SOURCES for model adapter acceptance")
    return Path(value)


@pytest.fixture(scope="module")
def reports(bundle):
    return {name: build_report(bundle, name) for name in ("baseline", "wider_network", "restore_geometry",
            "restore_and_network", "insufficient_power", "smaller_gang", "no_change")}


@pytest.fixture(scope="module")
def cases(bundle):
    return exercise(bundle)


def reidentify(report):
    report["evidence_digest"] = digest(report["evidence"])
    report["report_id"] = digest({key: value for key, value in report.items() if key != "report_id"})
    return report


def test_actual_fragmentation_and_bandwidth_no_benefit(reports):
    for name in ("baseline", "wider_network"):
        report = reports[name]
        assert report["selected_request_result"]["decision"] == "no-rectangle"
        assert report["engineering_readiness"] == "refused"
        assert report["evidence"]["raw"]["selected_cooling"]["verdict"] == "ADMIT"
        assert report["evidence"]["model_checks"]["healthy_step"]["state"] == "not_run"
        assert report["accounting"]["buckets"]["useful_compute_gpu_h"] == 0
        assert report["comparison"]["cohort_delta_gpu_h"] == 0
        assert report["recommendation"] == "wait"


def test_healthy_model_is_not_execution_proof(reports):
    report = reports["restore_geometry"]
    assert report["engineering_readiness"] == "feasible_within_profile"
    assert report["selected_request_result"]["decision"] == "allocated"
    assert report["accounting"]["buckets"]["useful_compute_gpu_h"] == pytest.approx(210430.58465522234)
    assert reports["restore_and_network"]["accounting"]["buckets"]["useful_compute_gpu_h"] == pytest.approx(218786.03266876104)
    for report in reports.values():
        assert report["execution_readiness"] == "incomplete"
        assert report["mutation_request"] is None
        assert report["proof_levels"] == {"model": "evaluated", "cpu_executor": "not_run", "operator": "missing"}
        assert report["comparison"]["selected_request_gain_gpu_h"] is None
        assert report["action_intent"] == trusted_profile()["action_intent"]
        assert report["resource_requirements"]["host_memory"]["required"] is None


def test_64_chip_memory_negative_is_incomparable(reports):
    report = reports["smaller_gang"]
    assert report["evidence"]["model_checks"]["sequential_geometry"]["state"] == "pass"
    assert report["evidence"]["model_checks"]["rough_gpu_memory"]["state"] == "fail"
    assert report["engineering_readiness"] == "refused"
    assert report["comparison"]["state"] == "incomparable"
    assert report["comparison"]["cohort_delta_gpu_h"] is None
    assert report["recommendation"] == "escalate"


def test_power_refusal_and_correct_no_change(reports):
    report = reports["insufficient_power"]
    assert report["evidence"]["raw"]["selected_cooling"]["verdict"] == "DENY"
    assert report["evidence"]["model_checks"]["sequential_geometry"]["state"] == "not_run"
    assert report["engineering_readiness"] == "refused"
    no_change = reports["no_change"]
    assert no_change["comparison"]["candidate_id"] == no_change["comparison"]["baseline_id"]
    assert no_change["comparison"]["cohort_delta_gpu_h"] == 0
    assert no_change["recommendation"] == "retain_current_state"


def test_four_bucket_conservation_and_raw_roundtrip(reports):
    for report in reports.values():
        roundtrip = loads(dumps(report))
        assert validate_report(roundtrip) == report
        assert sum(report["accounting"]["buckets"].values()) == pytest.approx(368640, abs=1e-6)
        assert report["accounting"]["buckets"] == report["evidence"]["raw"]["candidate"]["buckets"]
        assert report["accounting"]["checkpoint_gpu_second_ledger_included"] is False
        imports = report["evidence"]["sources"]["imports"]
        assert any(row["path"] == "cooling-pue-ladder/cooling/admission.py" for row in imports)
        assert all(not Path(row["path"]).is_absolute() for row in imports)


def test_owner_fixture_outputs_preserve_specific_semantics(cases):
    assert cases["result"]["all_match"]
    assert len(cases["result"]["cases"]) >= 58
    rows = {row["case_id"]: row for row in cases["result"]["cases"]}
    assert rows["resource/stale_capacity"]["raw"]["check_state"] == "stale"
    assert rows["resource/missing_gpu_peak"]["raw"]["check_state"] == "missing"
    assert rows["resource/not-run-capacity"]["raw"]["check_state"] == "not_run"
    assert rows["resource/bool-is-invalid"]["raw"]["check_state"] == "invalid"
    assert rows["resource/checkpoint_bound_insufficient"]["raw"]["engineering_readiness"] == "incomplete"
    assert rows["resource/device_a_imbalance"]["raw"]["check_state"] == "fail"
    assert rows["resource/device_b_imbalance"]["raw"]["check_state"] == "pass"
    assert rows["resource/pinned_subset_limit"]["raw"]["resource"] == "pinned_host_memory"
    assert rows["coverage/shortfall-with-other-device-missing"]["raw"]["missing_requirements"]
    assert rows["coverage/shortfall-with-other-device-missing"]["raw"]["resource_readiness"] == "refused"
    assert rows["coverage/omitted-device"]["raw"]["not_checked"]
    assert rows["calibration/wrong-statistic"]["raw"]["absolute_error"] is None
    assert rows["calibration/wrong-unit"]["raw"]["comparison"] == "incomparable"
    assert rows["calibration/measured-zero-no-relative-ratio"]["raw"]["relative_absolute_error"] is None
    assert rows["calibration/stale-at-expiry-equality"]["raw"]["assessment"] == "incomplete"
    assert rows["checkpoint/unknown-interval-coverage"]["raw"]["buckets"] is None
    assert rows["checkpoint/overlap-conservation"]["raw"]["buckets"] == {
        "checkpoint_only_gpu_s": 10, "recovery_gpu_s": 40, "healthy_gpu_s": 150}
    span = rows["span/ttl-equality-with-unchecked-plant"]["raw"]
    assert span["not_checked"]
    assert "FC2" not in {row["rule_id"] for row in span["findings"]}


def test_exact_resource_result_survives_rounding_and_bytes(bundle):
    def q(value, unit="cpu_core"):
        return {"value": value, "unit": unit, "state": "known", "source_id": "synthetic", "evidence_class": "synthetic_contract_fixture"}
    request = {"kind": "capacity", "input": {"scope_id": "node-a", "resource": "cpu",
              "demand_components": {"peak": q(1.0)}, "available": q(1.0), "margin": q(2**-54)}}
    result = evaluate(bundle, request)["result"]["raw"]
    assert result["required"] == 1.0  # rounded output must not recompute the exact predicate
    assert result["check_state"] == "fail"
    request["input"].update(resource="host_memory", demand_components={"peak": q(2**80, "byte")},
                            available=q(2**80 + 1, "byte"), margin=q(1, "byte"))
    result = evaluate(bundle, request)["result"]["raw"]
    assert type(result["required"]) is int and result["required"] == 2**80 + 1
    assert result["check_state"] == "pass"


@pytest.mark.parametrize("change", [
    lambda r: r.update(execution_readiness="feasible"),
    lambda r: r.update(profile_id="unreviewed-profile"),
    lambda r: r["action_intent"].update(designated_scheduler_scope="somewhere"),
    lambda r: r["action_intent"].pop("workload_artifact_digest"),
    lambda r: r["action_intent"].update(cardinality=True),
    lambda r: r["action_intent"]["approval"].update(status="approved"),
    lambda r: r["evidence"]["execution_checks"].pop("host_cpu"),
    lambda r: r["evidence"]["execution_checks"]["host_memory"].update(state="inapplicable"),
    lambda r: r["evidence"]["model_checks"]["sequential_geometry"].update(state="pass"),
    lambda r: r["accounting"]["buckets"].update(useful_compute_gpu_h=999999999),
    lambda r: r["accounting"].update(unit="GPU-s"),
    lambda r: r["comparison"].update(cohort_delta_gpu_h=999999999, selected_request_gain_gpu_h=999999999),
    lambda r: r.update(claim_limits=[]),
    lambda r: r["scope"].update(operator_scheduler="invented", operator_topology="invented"),
    lambda r: r["evidence"]["sources"]["repositories"]["cooling-pue-ladder"].update(commit="0" * 40),
    lambda r: r["evidence"]["sources"]["imports"][0].update(sha256="0" * 64),
    lambda r: r["evidence"]["execution_checks"]["host_cpu"].update(authenticated=True),
    lambda r: r.update(recommendation="execute_now"),
    lambda r: r.update(optional_checks={}),
    lambda r: r.update(case_id="no_change"),
    lambda r: r["evidence"]["raw"].update(declared_interventions=["invented_intervention"]),
    lambda r: r["evidence"]["sources"]["runtime"].update(numpy="invented"),
])
def test_rehashed_report_cannot_weaken_offline_contract(reports, change):
    report = deepcopy(reports["baseline"])
    change(report)
    with pytest.raises(ValueError):
        validate_report(reidentify(report))


def test_transport_binding_hashes_exact_bytes_without_claiming_approval(reports):
    content = dumps(reports["baseline"]).encode()
    request = policy_input(content, "receiver-roundtrip-0")
    assert request["report_binding"]["report_digest"] == "sha256:" + hashlib.sha256(content).hexdigest()
    assert request["report_binding"]["report_id"] == reports["baseline"]["report_id"]
    assert request["expected_binding"] is None
    assert request["approval"]["status"] == "not_requested"
    assert request["report_binding"]["target_scope"] is None
    assert policy_input(content + b"\n", "receiver-roundtrip-0")["report_binding"]["report_digest"] != request["report_binding"]["report_digest"]


def test_import_name_cannot_be_bound_to_another_locked_file(reports):
    report = deepcopy(reports["restore_geometry"])
    imports = report["evidence"]["sources"]["imports"]
    ladder = next(row for row in imports if row["module"] == "cooling.ladder")
    admission = next(row for row in imports if row["module"] == "cooling.admission")
    admission.update(path=ladder["path"], sha256=ladder["sha256"])
    with pytest.raises(ValueError, match="module name"):
        validate_report(reidentify(report))


@pytest.mark.parametrize("value", [True, -1, 10**1000])
def test_rebuilt_report_rejects_invalid_allocated_quantities(reports, value):
    evidence = deepcopy(reports["restore_geometry"]["evidence"])
    evidence["raw"]["candidate"]["jobs"][0]["compute_step_s"] = value
    with pytest.raises(ValueError, match="invalid healthy-step"):
        _assemble(evidence["raw"], evidence["sources"])


@pytest.mark.parametrize("value", [True, -1, 400000])
def test_rebuilt_report_rejects_invalid_baseline_ledger(reports, value):
    evidence = deepcopy(reports["restore_geometry"]["evidence"])
    evidence["raw"]["baseline"]["buckets"]["useful_compute_gpu_h"] = value
    with pytest.raises(ValueError, match="baseline accounting"):
        _assemble(evidence["raw"], evidence["sources"])


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"value":NaN}', '{"value":Infinity}', '{"value":1e999}',
                                  "[" * 65 + "0" + "]" * 65, '"' + "x" * (4 * 1024 * 1024) + '"'])
def test_strict_json_boundary(text):
    with pytest.raises(ValueError):
        loads(text)


def test_source_tamper_and_import_shadow_rejected(bundle, tmp_path):
    target = tmp_path / "sources"
    shutil.copytree(bundle, target)
    locked = target / "cooling-pue-ladder/cooling/admission.py"
    original = locked.read_bytes()
    locked.write_bytes(original + b"\n#changed\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_bundle(target)
    locked.write_bytes(original)
    shadow = target / "cooling-pue-ladder/cooling/shadow.so"
    shadow.write_bytes(b"unlocked")
    with pytest.raises(ValueError, match="unexpected source"):
        verify_bundle(target)


def test_worker_ignores_parent_import_and_pythonpath(bundle, tmp_path, monkeypatch):
    (tmp_path / "capacity.py").write_text("raise RuntimeError('unexpected import shadow')")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    report = build_report(bundle)
    assert report["engineering_readiness"] == "refused"


def test_public_cli_rejects_nonfinite_input(bundle):
    result = subprocess.run([sys.executable, "-I", "-m", "dimaggi_receiver.cli", "evaluate", "--sources", str(bundle)],
                            input='{"kind":"capacity","input":{"value":NaN}}', text=True, capture_output=True)
    assert result.returncode == 2
    assert loads(result.stdout)["mutation_request"] is None
