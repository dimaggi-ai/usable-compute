"""Derive the receiver report from actual owning-model results, never labels."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

from .adapters import call_owner
from .jsonio import canonical, digest
from .sources import source_lock

PROFILE_ID = "joined-local-torus-batch/v0.1"
VERSION = "dimaggi-receiver-report/v1"
COMPARISON_FIELDS = ("shape", "gang_size", "seq_len", "seed", "horizon_h", "nominal_gpu_h", "failure_rate_per_node_h")
BUCKETS = ("not_admitted_gpu_h", "recovery_and_discard_gpu_h",
           "retained_communication_and_bubble_gpu_h", "useful_compute_gpu_h")
REPORT_FIELDS = {"schema_version", "profile_id", "profile_digest", "evidence_digest", "evidence_class", "case_id",
                 "selected_request", "selected_request_result", "engineering_readiness", "execution_readiness",
                 "execution_authorized", "mutation_request", "recommendation", "scope", "comparison", "accounting",
                 "evidence", "optional_checks", "inapplicable_checks", "missing_execution_evidence", "resource_requirements",
                 "action_intent", "proof_levels", "claim_limits", "report_id"}


def trusted_profile():
    content = Path(__file__).with_name("profile.json").read_bytes()
    expected = source_lock()["repositories"]["research"]["files_sha256"]["integration/first_decision/profile.json"]
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("packaged profile differs from locked domain profile")
    return json.loads(content)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _model_checks(raw):
    candidate = raw["candidate"]
    jobs = [row for row in candidate["jobs"] if row["job"] == "job-0"]
    if len(jobs) != 1:
        raise ValueError("model must contain exactly one selected job-0")
    selected = jobs[0]
    if selected["decision"] not in {"allocated", "no-rectangle", "DENY", "STAGGER"}:
        raise ValueError("unknown selected model decision")
    memory = raw["memory"]
    if any(not _finite(memory[name]) or memory[name] < 0 for name in
           ("state_GB_per_device", "activation_GB_per_device", "estimated_GB_per_device", "configured_GB_per_device")):
        raise ValueError("memory screen must contain finite nonnegative quantities")
    if (memory["estimated_GB_per_device"] != memory["state_GB_per_device"] + memory["activation_GB_per_device"]
            or memory["rough_screen"] != ("pass" if memory["estimated_GB_per_device"] <= memory["configured_GB_per_device"] else "fail")
            or memory["unit"] != "GB = 10^9 bytes" or memory["evidence_class"] != "synthetic_model"):
        raise ValueError("memory screen mapping differs from raw quantities")
    if raw["selected_cooling"]["verdict"] not in {"ADMIT", "DENY", "STAGGER"}:
        raise ValueError("unknown cooling admission verdict")
    buckets = candidate["buckets"]
    conserved = (set(buckets) == set(BUCKETS)
                 and all(_finite(value) and value >= 0 for value in buckets.values())
                 and math.isclose(sum(buckets.values()), candidate["nominal_gpu_h"], rel_tol=0, abs_tol=1e-6))
    allocated = selected["decision"] == "allocated"
    if allocated and (not _finite(selected["healthy_step_s"]) or selected["healthy_step_s"] <= 0
                      or not _finite(selected["compute_step_s"])
                      or not 0 <= selected["compute_step_s"] <= selected["healthy_step_s"]
                      or not _finite(selected["recovery"]["productive_gpu_h"])
                      or not 0 <= selected["recovery"]["productive_gpu_h"] <= candidate["gang_size"] * candidate["horizon_h"]):
        raise ValueError("allocated model result has invalid healthy-step or recovery quantities")
    state = {
        "strict_input_validity": ("pass", "Fixed reviewed model inputs; strict JSON and source boundary verified."),
        "cooling_admission": ("pass" if raw["selected_cooling"]["verdict"] == "ADMIT" else "fail",
                              "Owning cooling admit result for job-0 at initial hall occupancy."),
        "sequential_geometry": ("pass" if allocated else "fail" if selected["decision"] == "no-rectangle" else "not_run",
                                "Owning sequential placement result; no real scheduler reservation."),
        "rough_gpu_memory": (raw["memory"]["rough_screen"], "Netcap rough estimate; a pass is not runtime fit."),
        "healthy_step": ("pass" if allocated and _finite(selected["healthy_step_s"]) and selected["healthy_step_s"] > 0
                         and _finite(selected["compute_step_s"])
                         and 0 <= selected["compute_step_s"] <= selected["healthy_step_s"] else "not_run",
                         "Healthy-step model runs only after in-memory allocation."),
        "recovery": ("pass" if allocated and _finite(selected["recovery"]["productive_gpu_h"])
                     and 0 <= selected["recovery"]["productive_gpu_h"] <= candidate["gang_size"] * candidate["horizon_h"]
                     else "not_run", "Recovery owns checkpoint/restart/discard once."),
        "conservation": ("pass" if conserved else "invalid", "Four cohort GPU-hour buckets conserve the same nominal denominator."),
        "source_identity": ("pass", "Locked source hashes and actual imported origins verified in a fresh process."),
    }
    refs = {"cooling_admission": "cooling-pue-ladder/cooling/admission.py::admit",
            "sequential_geometry": "scheduler-vs-more-gpus/capacity/placement.py::placement_preview",
            "rough_gpu_memory": "network-vs-more-gpus/src/netcap/performance.py::memory_per_accelerator_gb",
            "healthy_step": "network-vs-more-gpus/src/netcap/performance.py::step_breakdown",
            "recovery": "reliability-economics/sim/reliability_sim.py::Sim.run"}
    checks = {name: {"state": value, "reason": reason, "required": True,
                     "source": refs.get(name, "receiver/source-bound-model-adapter"),
                     "scope": "cohort" if name == "conservation" else "job-0",
                     "evidence_class": "synthetic_model", "freshness": "immutable_source_verified_this_run",
                     "measurement_window": None}
              for name, (value, reason) in state.items()}
    return selected, checks


def _readiness(checks, required):
    values = [checks.get(key, {}).get("state", "missing") for key in required]
    if any(value in ("fail", "invalid") for value in values):
        return "refused"
    return "feasible_within_profile" if all(value == "pass" for value in values) else "incomplete"


def build_report(sources, case_id="baseline"):
    envelope = call_owner(sources, "model", {"case_id": case_id})
    raw = dict(envelope["result"])
    if raw.pop("profile") != trusted_profile():
        raise ValueError("unsupported trusted source profile")
    report = _assemble(raw, envelope["sources"])
    return validate_report(report)


def _validate_source_metadata(identities):
    lock = source_lock()
    expected = {name: {"commit": row["commit"], "files_digest": digest(row["files_sha256"]),
                       "file_count": len(row["files_sha256"])} for name, row in lock["repositories"].items()}
    if (set(identities) != {"source_lock_digest", "repositories", "runtime", "integrity", "imports"}
            or identities["source_lock_digest"] != digest(lock)
            or canonical(identities["repositories"]) != canonical(expected)
            or identities["integrity"] != "verified local bytes; not source authenticity or operator truth"):
        raise ValueError("source metadata differs from installed lock")
    runtime = identities["runtime"]
    if (type(runtime) is not dict or set(runtime) != {"python", "numpy", "PyYAML", "jsonschema"}
            or not isinstance(runtime["python"], str) or not re.fullmatch(r"3\.\d+\.\d+", runtime["python"])
            or {name: runtime[name] for name in ("numpy", "PyYAML", "jsonschema")} !=
            {"numpy": "2.2.6", "PyYAML": "6.0.2", "jsonschema": "4.26.0"}):
        raise ValueError("unsupported runtime provenance")
    imports = identities["imports"]
    if type(imports) is not list or not imports:
        raise ValueError("actual imported origins required")
    seen = set()
    roots = {"cooling": "cooling-pue-ladder/", "capacity": "scheduler-vs-more-gpus/",
             "netcap": "network-vs-more-gpus/src/", "slicepacker": "slice-packer-torus/src/",
             "spancontract": "span-contract/src/"}
    standalone = {"receiver_joined_source": "usable-compute/integration/usable_capacity.py",
                  "joined_reliability": "reliability-economics/sim/reliability_sim.py",
                  "receiver_checkpoint_source": "reliability-economics/sim/checkpoint_evidence.py"}
    for row in imports:
        if type(row) is not dict or set(row) != {"module", "path", "sha256"}:
            raise ValueError("malformed imported origin")
        if not isinstance(row["module"], str) or row["module"] in seen or not isinstance(row["path"], str):
            raise ValueError("duplicate or malformed imported origin")
        seen.add(row["module"])
        if row["module"] in standalone:
            expected_paths = {standalone[row["module"]]}
        else:
            prefix = roots.get(row["module"].split(".")[0])
            if prefix is None:
                raise ValueError("unknown domain import namespace")
            module_path = prefix + row["module"].replace(".", "/")
            expected_paths = {module_path + ".py", module_path + "/__init__.py"}
        if row["path"] not in expected_paths:
            raise ValueError("module name and imported path disagree")
        repo, separator, relative = row["path"].partition("/")
        if (not separator or repo not in lock["repositories"]
                or lock["repositories"][repo]["files_sha256"].get(relative) != row["sha256"]):
            raise ValueError("imported origin differs from installed lock")
    required = {"receiver_joined_source", "joined_reliability", "cooling.admission", "cooling.ladder",
                "capacity.placement", "netcap.performance", "slicepacker.packing"}
    if not required.issubset(seen):
        raise ValueError("required actual imported entrypoints omitted")


def _assemble(raw, identities):
    """Shared presentation mapping, so validation cannot accept contradictory badges."""
    from ._worker import VARIANTS
    if type(raw) is not dict or set(raw) != {"case_id", "candidate", "baseline", "selected_cooling",
                                            "declared_interventions", "memory"}:
        raise ValueError("raw model fields differ")
    case_id = raw["case_id"]
    if not isinstance(case_id, str) or case_id not in VARIANTS:
        raise ValueError("unknown model case")
    baseline_parameters = {"degraded": False} if case_id == "no_change" else {}
    if raw["declared_interventions"] != sorted(set(VARIANTS[case_id]) | set(baseline_parameters)):
        raise ValueError("case intervention set differs")
    profile = trusted_profile()
    for key, overrides in (("candidate", VARIANTS[case_id]), ("baseline", baseline_parameters)):
        expected = {"feed_mw": .6, "degraded": True, "network_efficiency": .65, "reload_h": .25,
                    "seed": 0, "shape": [8, 8, 8], "gang_size": 128, "seq_len": 8192,
                    "failure_rate_per_node_h": 1.58e-4, "horizon_h": 720, "nominal_gpu_h": 368640,
                    "evidence_class": "simulated", "input_class": "scenario", **overrides}
        if any(canonical(raw[key].get(name)) != canonical(value) for name, value in expected.items()):
            raise ValueError("raw scenario parameters differ from reviewed case")
        buckets = raw[key]["buckets"]
        if (type(buckets) is not dict or set(buckets) != set(BUCKETS)
                or not all(_finite(value) and value >= 0 for value in buckets.values())
                or not math.isclose(sum(buckets.values()), raw[key]["nominal_gpu_h"], rel_tol=0, abs_tol=1e-6)):
            raise ValueError(f"{key} accounting is invalid or does not conserve its denominator")
    _validate_source_metadata(identities)
    selected, checks = _model_checks(raw)
    readiness = _readiness(checks, profile["checks"]["model_mandatory"])
    candidate, baseline = raw["candidate"], raw["baseline"]
    differences = [key for key in COMPARISON_FIELDS if candidate[key] != baseline[key]]
    comparable = not differences
    delta = candidate["buckets"]["useful_compute_gpu_h"] - baseline["buckets"]["useful_compute_gpu_h"] if comparable else None
    recommendation = "escalate" if not comparable or any(row["state"] == "invalid" for row in checks.values()) else "wait"
    if comparable and readiness == "feasible_within_profile" and delta <= 1e-6:
        recommendation = "retain_current_state"
    execution = {name: {"state": "missing", "required": True, "value": None,
                         "reason": "No authenticated operator or executor evidence supplied to this model-only receiver.",
                         "source_id": None, "observed_at_utc": None, "valid_until_utc": None}
                 for name in profile["checks"]["execution_additional_mandatory"]}
    evidence = {"raw": raw,
                "sources": identities, "model_checks": checks, "execution_checks": execution}
    report = {
        "schema_version": VERSION, "profile_id": PROFILE_ID, "profile_digest": digest(profile),
        "evidence_digest": digest(evidence), "evidence_class": "synthetic_model", "case_id": case_id,
        "selected_request": "job-0", "selected_request_result": selected,
        "engineering_readiness": readiness, "execution_readiness": "incomplete",
        "execution_authorized": False, "mutation_request": None,
        "recommendation": recommendation,
        "scope": {"kind": "hypothetical_local_torus", "shape_chips": candidate["shape"],
                  "request_order": [row["job"] for row in candidate["jobs"]],
                  "action_cardinality": 1, "cohort_is_context_only": True,
                  "operator_topology": None, "operator_scheduler": None},
        "comparison": {"state": "comparable" if comparable else "incomparable", "differing_fields": differences,
                       "declared_interventions": raw["declared_interventions"],
                       "cohort_delta_gpu_h": delta, "selected_request_gain_gpu_h": None,
                       "meaning": "Modeled retained compute service including recomputation; no completed-work, cost or operator claim.",
                       "baseline_id": digest(baseline), "candidate_id": digest(candidate),
                       "input_identity": digest({key: candidate[key] for key in COMPARISON_FIELDS}),
                       "runtime_and_sources_shared": True},
        "accounting": {"schema_version": "joined-capacity/v1", "unit": "GPU-h",
                       "horizon_h": candidate["horizon_h"], "nominal_gpu_h": candidate["nominal_gpu_h"],
                       "buckets": candidate["buckets"], "meanings": profile["output_meanings"],
                       "recovery_charged_once": True,
                       "checkpoint_gpu_second_ledger_included": False},
        "evidence": evidence,
        "optional_checks": {name: {"state": "not_run", "reason": "Not part of this single-seed receiver report."}
                            for name in profile["checks"]["model_optional"]},
        "inapplicable_checks": deepcopy(profile["checks"]["model_inapplicable"]),
        "missing_execution_evidence": list(execution),
        "resource_requirements": deepcopy(profile["resource_requirements"]),
        "action_intent": deepcopy(profile["action_intent"]),
        "proof_levels": {"model": "evaluated", "cpu_executor": "not_run", "operator": "missing"},
        "claim_limits": deepcopy(profile["prohibited_inferences"]),
    }
    report["report_id"] = digest(report)
    return report


def validate_report(report):
    try:
        return _validate_report(report)
    except (KeyError, TypeError, AttributeError, OverflowError, IndexError, RecursionError) as exc:
        raise ValueError("malformed receiver report") from exc


def _validate_report(report):
    """Validate local transport identity and offline invariants, not truth/auth.

    This is deliberately not a second domain evaluation or policy authority.
    Recomputing a hash after editing a report cannot turn it into live evidence.
    """
    canonical(report)
    if (type(report) is not dict or set(report) != REPORT_FIELDS
            or report.get("schema_version") != VERSION or report.get("profile_id") != PROFILE_ID):
        raise ValueError("unsupported receiver report/profile")
    expected = digest({key: value for key, value in report.items() if key != "report_id"})
    if report.get("report_id") != expected:
        raise ValueError("report identity differs from content")
    if report.get("evidence_digest") != digest(report["evidence"]):
        raise ValueError("evidence binding differs")
    if (report.get("evidence_class") != "synthetic_model" or report.get("selected_request") != "job-0"
            or report.get("execution_readiness") != "incomplete" or report.get("execution_authorized") is not False
            or report.get("mutation_request") is not None):
        raise ValueError("receiver is offline and model-only")
    evidence = report["evidence"]
    if set(evidence) != {"raw", "sources", "model_checks", "execution_checks"}:
        raise ValueError("required evidence fields differ")
    expected = _assemble(evidence["raw"], evidence["sources"])
    if canonical(report) != canonical(expected):
        raise ValueError("report presentation differs from raw results or trusted profile")
    return report
