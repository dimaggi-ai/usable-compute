"""Reproducible offline demonstration of Maggie's first bounded domain package.

Original model runs use the clean audited repositories. Additional domain
references run in isolated Python processes from their owning working checkout.
No scheduler API, credentials, network request or workload execution is used.
"""
from __future__ import annotations

import argparse
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


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_demo(workspace, out):
    if out.exists() and any(out.iterdir()):
        raise ValueError("choose an empty output directory; earlier evidence is preserved")
    working = workspace / "projects/infrastructure/DIMAGGI-Infrastructure-Intelligence"
    audit = workspace / "projects/infrastructure/_Audits/2026-09-17-repo-aware-strategy/repos"
    reference_path = working / "research/integration/first_decision/reference.py"
    spec = importlib.util.spec_from_file_location("maggie_existing_reference", reference_path)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    sources = reference.provenance(audit)
    joined = reference.load_joined(audit)
    imported = {}
    for name, module, repo in (("joined", joined, "usable-compute"),
                               ("joined_reliability", joined.reliability_module(), "reliability-economics")):
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(audit / repo):
            raise ValueError(f"unexpected imported model source for {name}: {path}")
        imported[name] = str(path.relative_to(workspace))
    for name, repo in (("capacity.placement", "scheduler-vs-more-gpus"), ("cooling.admission", "cooling-pue-ladder"),
                       ("netcap.performance", "network-vs-more-gpus"), ("slicepacker.packing", "slice-packer-torus")):
        path = Path(importlib.import_module(name).__file__).resolve()
        if not path.is_relative_to(audit / repo):
            raise ValueError(f"unexpected imported model source for {name}: {path}")
        imported[name] = str(path.relative_to(workspace))
    variants = {"baseline": {}, "wider_network": {"network_efficiency": .9},
                "restore_geometry": {"degraded": False},
                "restore_and_network": {"degraded": False, "network_efficiency": .9},
                "insufficient_power": {"degraded": False, "feed_mw": .1},
                "smaller_gang": {"gang_size": 64}}
    runs = {name: joined.scenario(seed=0, **args) for name, args in variants.items()}
    memory = {str(g): reference.memory_screen(joined, g) for g in (128, 64)}
    out.mkdir(parents=True, exist_ok=True)
    dump(out / "model-runs.json", runs)
    dump(out / "memory-screen.json", memory)
    commands = []

    def child(repo, args):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        command = [sys.executable, *args]
        result = subprocess.run(command, cwd=working / repo, env=env, text=True, capture_output=True, check=True)
        commands.append({"cwd": str((working / repo).relative_to(workspace)), "argv": command,
                         "exit_code": result.returncode, "stderr": result.stderr})
        return result.stdout

    child("scheduler-vs-more-gpus", ["-m", "capacity.resource_evidence_examples", "--out", str(out / "resource-cases.json"),
                                   "--netcap-root", str(audit / "network-vs-more-gpus")])
    dump(out / "calibration-cases.json", json.loads(child("scheduler-vs-more-gpus", ["-m", "capacity.calibration_examples"])))
    dump(out / "checkpoint-cases.json", json.loads(child("reliability-economics", ["sim/checkpoint_evidence.py"])))
    claims = [
        {"id": "C01", "class": "synthetic_model", "claim": "The selected degraded geometry admits zero actual chips despite a count-only estimate of 256.",
         "artifact": "model-runs.json#/baseline", "limit": "Hypothetical rectangular torus, not an observed scheduler error."},
        {"id": "C02", "class": "synthetic_model", "claim": "Changing network efficiency alone leaves that geometry refusal unchanged.",
         "artifact": "model-runs.json#/wider_network", "limit": "A changed model fraction is not a measured network upgrade."},
        {"id": "C03", "class": "synthetic_model", "claim": "Healthy geometry under the same feed permits three 128-chip gangs; the fourth is power-limited.",
         "artifact": "model-runs.json#/restore_geometry", "limit": "Counterfactual cohort; no repair authority or single-job gain is established."},
        {"id": "C04", "class": "synthetic_model", "claim": "The attractive 64-chip alternative fails the existing rough memory screen.",
         "artifact": "memory-screen.json#/64", "limit": "A different recipe/offload model requires a new explicit decision; 128-chip screen pass does not establish runtime fit."},
        {"id": "C05", "class": "synthetic_contract_fixture", "claim": "Scoped resource predicates preserve shortages, unknowns, stale evidence and lower-bound limits.",
         "artifact": "resource-cases.json", "limit": "Supplied quantities, not telemetry, scope completeness or authority."},
        {"id": "C06", "class": "synthetic_contract_fixture", "claim": "Calibration comparison requires matched quantities and explicit tolerance; wrong statistics are incomparable.",
         "artifact": "calibration-cases.json", "limit": "No operator observation or model fitting; tolerance agreement is not resource safety."},
        {"id": "C07", "class": "synthetic_contract_fixture", "claim": "Checkpoint phases remain distinct and a separate blocked-time ledger counts overlap once.",
         "artifact": "checkpoint-cases.json", "limit": "Owning schema labels these synthetic; no actual storage durability, restore or retained-progress improvement."},
        {"id": "C08", "class": "proposed_work", "claim": "CPU executor proof and operator proof remain future gates.",
         "artifact": None, "limit": "No real submission, GPU experiment, operator baseline, performance result or commercial benefit is claimed."},
    ]
    dump(out / "claims.json", {"schema_version": "maggie-demo-claims/v0.1", "claims": claims,
                              "execution_authorized": False, "mutation_request": None})
    lines = ["# First bounded batch decision — model demonstration", "",
             "Generated offline from pinned models and hashed Maggie reference files. All numerical examples are modeled or synthetic.", "",
             "**Decision: wait.** The selected request `job-0` fails the model geometry check. Operator and executor profiles remain incomplete; no action is emitted.", "",
             "| Model alternative (seed 0) | Requests | Count-only chips | Allocated chips | Retained compute GPU-h |",
             "|---|---:|---:|---:|---:|"]
    for name, run in runs.items():
        lines.append(f"| {name} | {len(run['jobs'])} | {run['count_only_admission_gpus']} | {run['actual_allocated_gpus']} | {run['buckets']['useful_compute_gpu_h']:.6f} |")
    lines += ["", "The 128-chip rows have four requests; the 64-chip row has eight. All use 512 × 720 = 368,640 nominal GPU-hours. Retained compute includes recompute; it is not completed training work. The smaller gang changes PP/work mapping and cannot justify a same-workload gain.", "",
              f"The reused rough device-memory screen estimates {memory['128']['estimated_GB_per_device']:.9f} GB for 128 chips and {memory['64']['estimated_GB_per_device']:.9f} GB for 64, against a modeled 80 GB/device. Host CPU/RAM, actual peak device memory, storage and checkpoint service remain unknown.", "",
              "## Demonstration sequence", "",
              "1. Inspect C01/C02: count and network enthusiasm cannot override failed geometry.",
              "2. Inspect C03/C04: changing the binding constraint changes the result, but a superficially attractive smaller gang fails the memory screen.",
              "3. Inspect C05/C06: show a scoped resource refusal, unknown evidence, expiry at equality and a mismatched-statistic refusal to compare.",
              "4. Inspect C07: staged is not committed, committed is not restored; recovery/checkpoint overlap contributes 40 + 10 GPU-s, leaving 150 GPU-s unblocked in a separate 200 GPU-s ledger.",
              "5. End with C08: CPU execution mechanics and representative operator evidence are not present.", "",
              "See [claim record](claims.json), [source and run manifest](provenance.json), [model rows](model-runs.json), [resource cases](resource-cases.json), [calibration cases](calibration-cases.json) and [checkpoint cases](checkpoint-cases.json). No video or publication was produced."]
    (out / "Demo.md").write_text("\n".join(lines) + "\n")
    roots = [working / "research/integration/first_decision", working / "research/integration/maggie_demo"]
    files = [p for root in roots for p in root.iterdir() if p.is_file()]
    for relative in ["scheduler-vs-more-gpus/capacity/resource_evidence.py", "scheduler-vs-more-gpus/capacity/resource_evidence_examples.py",
                     "scheduler-vs-more-gpus/capacity/calibration_evidence.py", "scheduler-vs-more-gpus/capacity/calibration_examples.py",
                     "scheduler-vs-more-gpus/capacity/calibration_input_template.json", "reliability-economics/sim/checkpoint_evidence.py"]:
        files.append(working / relative)
    dump(out / "provenance.json", {"audited_repositories": sources, "imported_model_files": imported,
         "runtime": {"python": platform.python_version(), "executable": sys.executable,
                     "numpy": version("numpy"), "PyYAML": version("PyYAML"), "jsonschema": version("jsonschema")}, "reference_commands": commands,
         "working_reference_sha256": {str(p.relative_to(workspace)): digest(p) for p in files},
         "artifact_sha256": {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()},
         "authenticity": "Local reproducibility record only, not a signed or authorized action receipt."})
    return {"output": str(out), "model_cases": len(runs), "claims": len(claims), "execution_authorized": False, "mutation_request": None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_demo(args.workspace_root.resolve(), args.out.resolve()), indent=2))
