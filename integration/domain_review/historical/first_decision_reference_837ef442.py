"""Offline Maggie reference: existing models plus explicit evidence meanings.

Not an application adapter, authority validator, scheduler or resource simulator.
All outputs are synthetic/model-only and all mutation requests are null.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PINS = {
    "usable-compute": "eca071496c8ef52c5144261aaa02a386d2361c6d",
    "scheduler-vs-more-gpus": "1853bd219e79b437b0c310fb40049ad272ceaf98",
    "network-vs-more-gpus": "3047ddb4f11e65c46b5bcab6f7674dd9fb3900e4",
    "reliability-economics": "92a4cd692d32ecc03943556fefc2317f53bde086",
    "cooling-pue-ladder": "4b94c6d6fc653e9eebd2933402826cd76b3a108d",
    "slice-packer-torus": "acc05b9d209a6f61df161634e929b6e6931c3d01",
    "span-contract": "e8a76e4af90a2573d7ea3ec4dbe1052c647d8c01",
}
STATES = {"pass", "fail", "invalid", "missing", "stale", "not_run", "inapplicable"}
DEPENDENCIES = {"cooling": "cooling-pue-ladder", "capacity": "scheduler-vs-more-gpus",
                "slicepacker": "slice-packer-torus", "netcap": "network-vs-more-gpus",
                "spancontract": "span-contract"}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_joined(repos):
    """Use the existing entry point, including its existing sibling imports."""
    # sys.path changes cannot replace an already cached package. Fail rather
    # than claim a pin while using a working or installed module's cached code.
    repos = repos.resolve()
    for name, module in list(sys.modules.items()):
        namespace = name.split(".")[0]
        if namespace in DEPENDENCIES:
            origin = getattr(module, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(repos / DEPENDENCIES[namespace]):
                raise ValueError(f"foreign cached module {name}; run pinned reference in a fresh Python process")
    path = repos / "usable-compute/integration/usable_capacity.py"
    spec = importlib.util.spec_from_file_location("maggie_pinned_joined", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.insert(0, str(repos / "span-contract/src"))
    return module


def provenance(repos):
    rows = {}
    for name, expected in PINS.items():
        root = repos / name
        commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        tracked = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"], text=True).strip()
        if commit != expected or tracked:
            raise ValueError(f"{name}: requires clean tracked files at audit pin {expected}")
        # Hash all tracked Python/config/schema inputs; commits alone do not bind dirty files.
        names = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
        files = {n: sha(root / n) for n in names if n and Path(n).suffix in {".py", ".json", ".yaml", ".yml"}}
        rows[name] = {"commit": commit, "tracked_status": tracked, "files_sha256": files}
    return rows


def classify(checks, required, *, comparable=True, delta=None):
    """Tiny executable meaning reference; supplied check states are NOT evidence.

    Ronnie must derive and bind these states from the owning model/observer.
    Missing required keys and mandatory 'inapplicable' never become a pass.
    """
    if any(state not in STATES for state in checks.values()):
        raise ValueError("unknown check state")
    if delta is not None and (type(delta) not in (int, float) or not math.isfinite(delta)):
        raise ValueError("delta must be finite or null")
    values = [checks.get(name, "missing") for name in required]
    if any(v in {"invalid", "fail"} for v in values):
        readiness = "refused"
    elif any(v != "pass" for v in values):
        readiness = "incomplete"
    else:
        readiness = "feasible_within_profile"
    comparison = "incomparable" if not comparable else ("not_requested" if delta is None else "comparable")
    recommendation = "wait"
    if "invalid" in values or not comparable:
        recommendation = "escalate"
    elif readiness == "feasible_within_profile" and delta is not None and delta <= 1e-6:
        recommendation = "retain_current_state"
    return {"engineering_readiness": readiness, "comparison": comparison,
            "recommendation": recommendation, "mutation_request": None}


def memory_screen(joined, gang_size, seq_len=8192):
    from netcap.performance import memory_per_accelerator_gb
    net = joined.load_scenario(joined.REPOS / "network-vs-more-gpus/configs/scenarios/reference_405b_16k.yaml")
    net = joined.replace_nested(net, **{"parallelism.dp": 1, "parallelism.pp": gang_size // 8,
                                       "model.seq_len": seq_len})
    state, activation = memory_per_accelerator_gb(net)
    return {"state_GB_per_device": state, "activation_GB_per_device": activation,
            "estimated_GB_per_device": state + activation,
            "configured_GB_per_device": net.accelerator.memory_gb,
            "rough_screen": "pass" if state + activation <= net.accelerator.memory_gb else "fail",
            "unit": "GB = 10^9 bytes", "evidence_class": "synthetic_model",
            "limit": "Rough screen only; no runtime peak, allocator reserve or memory headroom proof."}


def cooling_probe():
    from cooling.admission import Hall, Feeder, Start, admit
    from cooling.ladder import rung
    cases = [
        ("finite_limit", .01, 40., 40., 0.), ("nan_hall_feed", math.nan, 40., 40., 0.),
        ("finite_feeder", 8., .01, 40., 0.), ("nan_feeder", 8., math.nan, 40., 0.),
        ("finite_step", 8., 40., .01, 0.), ("nan_step", 8., 40., math.nan, 0.),
        ("finite_ride", 8., 40., 40., 1000.), ("nan_ride", 8., 40., 40., math.nan),
    ]
    rows = []
    for name, feed, ceiling, step, ride in cases:
        hall = Hall("h", "f", rung("direct-to-chip"), 100., 0, feed)
        result = admit(Start("j", {"h": 1}, ride), {"h": hall}, {"f": Feeder("f", ceiling, step)})
        rows.append({"case": name, "verdict": result.verdict, "reasons": list(result.reasons)})
    return rows


def reproduce(repos, out):
    sources = provenance(repos)
    joined = load_joined(repos)
    import numpy
    import yaml
    import jsonschema
    from importlib.metadata import version
    original = joined.experiment()
    variants = {"baseline": {}, "wider_network": {"network_efficiency": .9},
                "restore_geometry": {"degraded": False},
                "restore_and_network": {"degraded": False, "network_efficiency": .9},
                "insufficient_power": {"degraded": False, "feed_mw": .1},
                "smaller_gang": {"gang_size": 64}}
    runs = {name: joined.scenario(seed=0, **kw) for name, kw in variants.items()}
    memory = {str(gang): memory_screen(joined, gang) for gang in (64, 128)}
    cases = json.loads((HERE / "cases.json").read_text())
    answers = {}
    for case in cases["semantic_cases"]:
        got = classify(case["checks"], case["required"], comparable=case["comparable"], delta=case["delta_gpu_h"])
        if got != case["expected"]:
            raise AssertionError(f"known answer differs: {case['id']}")
        answers[case["id"]] = got
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "joined-capacity.json", original)
    dump(out / "selected-runs.json", runs)
    dump(out / "cooling-probe.json", cooling_probe())
    dump(out / "reference-report.json", {
        "schema_version": "maggie-first-decision-report/v0.1-proposed",
        "profile_id": "joined-local-torus-batch/v0.1",
        "evidence_class": "synthetic_model", "selected_request": "job-0",
        "scope": "First request geometry gate; aggregate cohort ledger is contextual only.",
        "model_result": "refused_no_rectangle", "recommendation": "wait",
        "execution_readiness": "incomplete", "mutation_request": None,
        "missing_execution_evidence": ["operator workload and topology", "scheduler scope and capability",
            "host CPU and memory", "runtime GPU-memory peak", "storage and checkpoint I/O",
            "fresh observed inventory and plant", "policy and permission"],
        "rough_gpu_memory": memory, "semantic_case_results": answers,
        "accounting": "512 * 720 = 368640 nominal GPU-hours; losses owned once",
    })
    dump(out / "provenance.json", {
        "repositories": sources, "runtime": {"python": platform.python_version(),
            "numpy": numpy.__version__, "PyYAML": yaml.__version__, "jsonschema": version("jsonschema")},
        "reference_files_sha256": {p.name: sha(p) for p in HERE.iterdir() if p.is_file()},
        "artifact_files_sha256": {p.name: sha(p) for p in out.iterdir() if p.name in {
            "joined-capacity.json", "selected-runs.json", "cooling-probe.json", "reference-report.json"}},
        "authenticity": "Local reproduction record, not a signed execution receipt.",
    })
    print(json.dumps({"original_means": original["mean_useful_compute_gpu_h"],
                      "memory": memory, "semantic_cases": len(answers)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    reproduce(args.repos_root.resolve(), args.out.resolve())
