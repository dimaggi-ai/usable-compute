"""Isolated source loader. Invoked by adapters in a fresh -I -B interpreter."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import importlib
import importlib.util
import json
from pathlib import Path
import sys

from .jsonio import dumps, loads
from .sources import imported_origin, verify_bundle

VARIANTS = {"baseline": {}, "wider_network": {"network_efficiency": .9},
            "restore_geometry": {"degraded": False},
            "restore_and_network": {"degraded": False, "network_efficiency": .9},
            "insufficient_power": {"degraded": False, "feed_mw": .1},
            "smaller_gang": {"gang_size": 64}, "no_change": {"degraded": False}}
NAMESPACES = {"cooling", "capacity", "netcap", "slicepacker", "spancontract"}


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def initialize(root):
    source = verify_bundle(root)
    if any(name.split(".")[0] in NAMESPACES for name in sys.modules):
        raise ValueError("domain module cached before verified source initialization")
    for repo in ("cooling-pue-ladder", "scheduler-vs-more-gpus", "network-vs-more-gpus",
                 "slice-packer-torus", "span-contract"):
        sys.path[:0] = [str(root / repo), str(root / repo / "src")]
    return source


def model(root, request):
    if set(request) != {"case_id"} or request["case_id"] not in VARIANTS:
        raise ValueError("model input requires one supported case_id; arbitrary profile overrides are unsupported")
    joined = load_file("receiver_joined_source", root / "usable-compute/integration/usable_capacity.py")
    from netcap.performance import memory_per_accelerator_gb
    case = request["case_id"]
    baseline_parameters = {"degraded": False} if case == "no_change" else {}
    candidate = joined.scenario(seed=0, **VARIANTS[case])
    baseline = joined.scenario(seed=0, **baseline_parameters)
    # Re-evaluate the selected first request directly to preserve the owner's
    # cooling result, which the original joined row intentionally elides on ADMIT.
    hall = joined.Hall("h", "f", joined.rung("direct-to-chip"), 81.6, 0, candidate["feed_mw"])
    cooling = joined.admit(joined.Start("job-0", {"h": candidate["gang_size"] / 64}, 5),
                           {"h": hall}, {"f": joined.Feeder("f", candidate["feed_mw"], candidate["feed_mw"])})
    net = joined.load_scenario(root / "network-vs-more-gpus/configs/scenarios/reference_405b_16k.yaml")
    net = joined.replace_nested(net, **{"parallelism.dp": 1, "parallelism.pp": candidate["gang_size"] // 8,
                                       "model.seq_len": candidate["seq_len"]})
    state, activation = memory_per_accelerator_gb(net)
    profile = json.loads((root / "research/integration/first_decision/profile.json").read_text())
    return {"case_id": case, "candidate": candidate, "baseline": baseline,
            "selected_cooling": asdict(cooling),
            "declared_interventions": sorted(set(VARIANTS[case]) | set(baseline_parameters)),
            "memory": {"state_GB_per_device": state, "activation_GB_per_device": activation,
                       "estimated_GB_per_device": state + activation,
                       "configured_GB_per_device": net.accelerator.memory_gb,
                       "rough_screen": "pass" if state + activation <= net.accelerator.memory_gb else "fail",
                       "unit": "GB = 10^9 bytes", "evidence_class": "synthetic_model",
                       "meaning": "Rough rejection screen; no runtime peak or headroom proof."},
            "profile": profile}


def evaluate(root, request):
    if type(request) is not dict or set(request) != {"kind", "input"} or type(request["input"]) is not dict:
        raise ValueError("adapter request requires exactly kind and object input")
    from capacity.resource_evidence import capacity_check, checkpoint_bound_check
    from capacity.resource_coverage import evaluate_coverage
    from capacity.calibration_evidence import compare_pair
    checkpoint = load_file("receiver_checkpoint_source", root / "reliability-economics/sim/checkpoint_evidence.py")
    functions = {"capacity": capacity_check, "checkpoint_bound": checkpoint_bound_check,
                 "coverage": evaluate_coverage, "calibration": compare_pair,
                 "checkpoint_phases": checkpoint.evaluate_checkpoint, "checkpoint_ledger": checkpoint.blocked_ledger}
    kind, arguments = request["kind"], request["input"]
    try:
        if kind == "span":
            from spancontract.envelope import SpanEnvelope
            from spancontract.rules import Policy
            from spancontract.validator import validate, audit_record
            if set(arguments) != {"envelope", "policy"}:
                raise ValueError("span input requires envelope and explicit policy mapping")
            policy = Policy(**arguments["policy"])
            envelope = SpanEnvelope.from_dict(arguments["envelope"])
            raw = audit_record(envelope, validate(envelope, policy), policy)
        elif kind in ("checkpoint_phases", "checkpoint_ledger"):
            raw = functions[kind](arguments)
        elif kind in functions:
            raw = functions[kind](**arguments)
        else:
            raise ValueError("unsupported adapter kind")
    except (ValueError, TypeError, KeyError) as exc:
        return {"kind": kind, "input": arguments, "raw": None,
                "error": {"type": type(exc).__name__, "message": str(exc)}, "check_state": "invalid"}
    if kind in ("capacity", "checkpoint_bound"):
        state = raw["check_state"]
    elif kind == "coverage":
        state = {"refused": "fail", "incomplete": "not_run",
                 "feasible_within_declared_resource_checks": "pass"}[raw["resource_readiness"]]
    elif kind == "calibration":
        state = {"invalid": "invalid", "incomplete": "not_run", "incomparable": "not_run",
                 "within_supplied_tolerance": "pass", "outside_supplied_tolerance": "fail"}[raw["assessment"]]
    elif kind == "span":
        state = "fail" if raw["decision"] not in ("local", "span") else "not_run" if raw["not_checked"] else "pass"
    else:
        state = "not_run" if raw["evaluation"] == "incomplete" else "fail" if raw.get("restore_verified") is False else "pass"
    return {"kind": kind, "input": arguments, "raw": raw, "error": None, "check_state": state}


def exercise(root):
    from capacity.resource_evidence_examples import fixtures as resource_fixtures
    from capacity.coverage_examples import fixtures as coverage_fixtures
    from capacity.calibration_examples import fixtures as calibration_fixtures
    checkpoint = load_file("receiver_checkpoint_source", root / "reliability-economics/sim/checkpoint_evidence.py")
    rows = []
    def add(case_id, kind, arguments, expected, split="exposed_regression"):
        result = evaluate(root, {"kind": kind, "input": arguments})
        rows.append({"case_id": case_id, "split": split, "expected_check_state": expected,
                     "matches_expected": result["check_state"] == expected, **result})
    resource_cases = resource_fixtures()["cases"]
    for case in resource_cases:
        add("resource/" + case["id"], case["kind"], case["input"], case["expected_check_state"])
    invalid = deepcopy(resource_cases[0]["input"])
    invalid["available"]["value"] = True
    add("resource/bool-is-invalid", "capacity", invalid, "invalid")
    waived = deepcopy(resource_cases[0]["input"])
    waived["margin"].update(value=None, state="inapplicable")
    add("resource/mandatory-margin-cannot-be-waived", "capacity", waived, "invalid")
    missing = deepcopy(resource_cases[0]["input"])
    missing.pop("margin")
    add("resource/omitted-mandatory-argument", "capacity", missing, "invalid")
    unrun = deepcopy(resource_cases[0]["input"])
    unrun["available"].update(value=None, state="not_run")
    add("resource/not-run-capacity", "capacity", unrun, "not_run")
    for case in coverage_fixtures():
        expected = {"incomplete": "not_run", "refused": "fail", "feasible_within_declared_resource_checks": "pass"}[case["expected_resource_readiness"]]
        add("coverage/" + case["id"], "coverage", case["input"], expected)
    coverage = deepcopy(coverage_fixtures()[0]["input"])
    coverage["bundle"]["checks"].append(deepcopy(coverage["bundle"]["checks"][6]))
    add("coverage/duplicate-shared-storage", "coverage", coverage, "invalid")
    coverage = deepcopy(coverage_fixtures()[0]["input"])
    coverage["bundle"]["workload_id"] = "different-workload"
    add("coverage/changed-binding", "coverage", coverage, "invalid")
    for case in calibration_fixtures():
        expected = {"invalid": "invalid", "incomplete": "not_run", "incomparable": "not_run", "within_supplied_tolerance": "pass", "outside_supplied_tolerance": "fail"}[case["expected_assessment"]]
        add("calibration/" + case["id"], "calibration", case["input"], expected)
    calibration = deepcopy(calibration_fixtures()[0]["input"])
    calibration["observation"]["observed_at_utc"] = "2026-09-19T12:00:00.0000001Z"
    add("calibration/unsupported-time-precision", "calibration", calibration, "invalid")
    calibration = deepcopy(calibration_fixtures()[0]["input"])
    calibration["observation"]["context"]["topology_id"] = "different-topology"
    add("calibration/wrong-topology", "calibration", calibration, "not_run")
    cases = checkpoint.synthetic_cases()
    for record, expected in zip(cases["phases"], ("pass", "not_run", "fail", "not_run")):
        add("checkpoint/" + record["case_id"], "checkpoint_phases", record, expected)
    for record in cases["intervals"]:
        add("checkpoint/" + record["case_id"], "checkpoint_ledger", record, "pass")
    record = deepcopy(cases["phases"][0]); record["restore"]["checkpoint_id"] = "other-checkpoint"
    add("checkpoint/changed-restore-binding", "checkpoint_phases", record, "invalid")
    record = deepcopy(cases["phases"][0]); record["commit"]["committed_s"] = 1
    add("checkpoint/commit-before-stage", "checkpoint_phases", record, "invalid")
    record = deepcopy(cases["intervals"][0]); record["interval_coverage"] = "unknown"
    add("checkpoint/unknown-interval-coverage", "checkpoint_ledger", record, "not_run")
    span = json.loads((root / "span-contract/examples/campus-stitch-pipeline.json").read_text())
    span["measured_age_s"] = 300
    add("span/ttl-equality-with-unchecked-plant", "span", {"envelope": span, "policy": {"measurement_ttl_s": 300}}, "not_run")
    return {"cases": rows, "all_match": all(row["matches_expected"] for row in rows),
            "held_out": False, "evidence_class": "synthetic_contract_fixture"}


def run(root, request):
    source = initialize(root)
    if type(request) is not dict or set(request) != {"operation", "input"}:
        raise ValueError("worker request requires operation and input")
    operation = request["operation"]
    if operation == "model":
        result = model(root, request["input"])
    elif operation == "evaluate":
        result = evaluate(root, request["input"])
    elif operation == "exercise" and request["input"] == {}:
        result = exercise(root)
    else:
        raise ValueError("unsupported worker operation")
    source["imports"] = [imported_origin(root, name, module)
                         for name, module in sorted(sys.modules.items())
                         if name.split(".")[0] in NAMESPACES or name in
                         ("receiver_joined_source", "joined_reliability", "receiver_checkpoint_source")]
    return {"result": result, "sources": source}


def main():
    try:
        root = Path(sys.argv[1]).resolve(strict=True)
        result = run(root, loads(sys.stdin.read()))
        print(dumps(result), end="")
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(dumps({"error": {"type": type(exc).__name__, "message": str(exc)}}), end="")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
