"""Offline domain acceptance: pinned joined model with reviewed cooling substitution.

Only the five imported cooling entry points are substituted. The joined model,
placement, healthy-step and recovery implementations remain at the audit pins.
This is a declared model compatibility experiment, never executor proof.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def load_module(name, path, *, package=False):
    kwargs = {"submodule_search_locations": [str(path.parent)]} if package else {}
    spec = importlib.util.spec_from_file_location(name, path, **kwargs)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def corrected_probe(admission, ladder):
    rows = []
    cases = [("hall_feed", .01, 40., 40., 0.), ("feeder_limit", 8., .01, 40., 0.),
             ("feeder_step", 8., 40., .01, 0.), ("ride_requirement", 8., 40., 40., 1000.)]
    for index, (name, *control) in enumerate(cases):
        for invalid in (False, True):
            values = list(control)
            if invalid:
                values[index] = float("nan")
            feed, ceiling, step, ride = values
            try:
                hall = admission.Hall("h", "f", ladder.rung("direct-to-chip"), 100., 0, feed)
                verdict = admission.admit(admission.Start("j", {"h": 1}, ride), {"h": hall},
                                          {"f": admission.Feeder("f", ceiling, step)}).verdict
                outcome = verdict
            except ValueError:
                outcome = "invalid_input"
            expected = "invalid_input" if invalid else "DENY"
            if outcome != expected:
                raise AssertionError((name, invalid, outcome, expected))
            rows.append({"case_id": name + ("/NaN" if invalid else "/finite-control"),
                         "input_class": "synthetic_boundary_case", "outcome": outcome})
    return rows


def run(workspace, out):
    started = datetime.now(timezone.utc).isoformat()
    if out.exists() and any(out.iterdir()):
        raise ValueError("review output must be empty; historical evidence is preserved")
    working = workspace / "projects/infrastructure/DIMAGGI-Infrastructure-Intelligence"
    audited = workspace / "projects/infrastructure/_Audits/2026-09-17-repo-aware-strategy/repos"
    reference = load_module("review_first_decision", working / "research/integration/first_decision/reference.py")
    pinned_sources = reference.provenance(audited)
    joined = reference.load_joined(audited)
    # Keep the entire pinned import path explicit, including package caches.
    origins = {}
    for name, repo in [("cooling.admission", "cooling-pue-ladder"), ("capacity.placement", "scheduler-vs-more-gpus"),
                       ("netcap.performance", "network-vs-more-gpus"), ("slicepacker.packing", "slice-packer-torus")]:
        path = Path(importlib.import_module(name).__file__).resolve()
        if not path.is_relative_to(audited / repo):
            raise ValueError(f"unexpected pinned import {name}: {path}")
        origins[name] = str(path.relative_to(workspace))
    variants = {"baseline": {}, "wider_network": {"network_efficiency": .9},
                "restore_geometry": {"degraded": False}, "restore_and_network": {"degraded": False, "network_efficiency": .9},
                "insufficient_power": {"degraded": False, "feed_mw": .1}, "smaller_gang": {"gang_size": 64}}
    baseline = {f"{name}/seed-{seed}": joined.scenario(seed=seed, **args)
                for name, args in variants.items() for seed in (0, 1, 2)}
    old_probe = reference.cooling_probe()
    load_module("review_working_cooling", working / "cooling-pue-ladder/cooling/__init__.py", package=True)
    admission = importlib.import_module("review_working_cooling.admission")
    ladder = importlib.import_module("review_working_cooling.ladder")
    for name in ("Hall", "Feeder", "Start", "admit"):
        setattr(joined, name, getattr(admission, name))
    joined.rung = ladder.rung
    corrected = {f"{name}/seed-{seed}": joined.scenario(seed=seed, **args)
                 for name, args in variants.items() for seed in (0, 1, 2)}
    if baseline != corrected:
        raise AssertionError("finite joined outputs differ after the reviewed cooling substitution")
    fixed_probe = corrected_probe(admission, ladder)
    old_nan = [row for row in old_probe if row["case"].startswith("nan_")]
    if len(old_nan) != 4 or any(row["verdict"] != "ADMIT" for row in old_nan):
        raise AssertionError("pinned known-issue reproduction changed")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONPATH", None)
    command = [sys.executable, "-m", "capacity.coverage_examples"]
    coverage_run = subprocess.run(command, cwd=working / "scheduler-vs-more-gpus", env=env,
                                  text=True, capture_output=True, check=True)
    coverage = json.loads(coverage_run.stdout)
    out.mkdir(parents=True, exist_ok=True)
    write(out / "joined-compatibility.json", {"evidence_class": "synthetic_model", "pairs": len(baseline),
          "equal": True, "baseline_runs": baseline, "corrected_runs": corrected,
          "substitution": ["Hall", "Feeder", "Start", "admit", "rung"],
          "limits": "Model compatibility only; no new calibration, workload completion or operator performance."})
    write(out / "cooling-correction.json", {"pinned_known_defect": old_probe, "corrected_working_results": fixed_probe,
          "execution_authorized": False, "mutation_request": None})
    write(out / "coverage-cases.json", coverage)
    report = {"schema_version": "maggie-domain-review/v0.1", "status": "local_domain_checks_passed",
              "finite_joined_pairs_equal": len(baseline), "known_nan_cases_now_invalid": 4,
              "coverage_cases": len(coverage["cases"]), "operator_evidence": "missing",
              "cpu_executor": "not_run", "execution_authorized": False, "mutation_request": None,
              "claim_limit": "This is independent local domain verification, not human receiver acceptance or execution permission."}
    write(out / "review-report.json", report)
    files = [Path(__file__), working / "research/integration/first_decision/reference.py"]
    files += list((working / "cooling-pue-ladder/cooling").glob("*.py"))
    files += [working / "scheduler-vs-more-gpus/capacity" / name for name in
              ("resource_coverage.py", "coverage_examples.py", "resource_evidence.py", "resource_evidence_examples.py")]
    write(out / "provenance.json", {"pinned_sources": pinned_sources, "pinned_imports": origins,
          "started_at_utc": started, "completed_at_utc": datetime.now(timezone.utc).isoformat(),
          "runtime": {"python": platform.python_version(), "executable": sys.executable,
                      "numpy": version("numpy"), "PyYAML": version("PyYAML"), "jsonschema": version("jsonschema")},
          "working_source_sha256": {str(p.relative_to(workspace)): sha(p) for p in files},
          "working_commits": {name: subprocess.check_output(["git", "-C", str(working / name), "rev-parse", "HEAD"], text=True).strip()
                              for name in ("research", "scheduler-vs-more-gpus", "cooling-pue-ladder")},
          "coverage_command": command, "coverage_exit_code": coverage_run.returncode,
          "artifact_sha256": {p.name: sha(p) for p in sorted(out.iterdir())},
          "authenticity": "Local hashes identify inputs, not authenticated physical truth or action receipts."})
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace_root.resolve(), args.out.resolve()), indent=2, allow_nan=False))
