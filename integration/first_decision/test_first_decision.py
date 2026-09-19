"""Independent arithmetic/known-answer checks, not evidence of operator benefit."""
import dataclasses
from datetime import datetime, timedelta
import json
import math
import os
from pathlib import Path
import sys
import types

import pytest

from reference import HERE, DEPENDENCIES, classify, cooling_probe, load_joined, memory_screen, provenance

REPOS = Path(os.environ.get("MAGGIE_REPOS_ROOT", HERE.parents[3] / "_Audits/2026-09-17-repo-aware-strategy/repos"))
CASES = json.loads((HERE / "cases.json").read_text())
PROFILE = json.loads((HERE / "profile.json").read_text())


@pytest.fixture(scope="module")
def joined():
    provenance(REPOS)
    # Other integration tests deliberately import working packages at collection.
    # Scope this audit-pin fixture's package cache and sys.path, then restore them.
    saved = {name: module for name, module in sys.modules.items() if name.split(".")[0] in DEPENDENCIES}
    saved_path = list(sys.path)
    for name in saved:
        del sys.modules[name]
    try:
        yield load_joined(REPOS)
    finally:
        for name in list(sys.modules):
            if name.split(".")[0] in DEPENDENCIES:
                del sys.modules[name]
        sys.modules.update(saved)
        sys.path[:] = saved_path


def test_pinned_loader_refuses_foreign_cached_module(joined, monkeypatch):
    foreign = types.ModuleType("cooling.foreign")
    foreign.__file__ = "/tmp/foreign-cooling/cooling/foreign.py"
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "cooling.foreign", foreign)
        with pytest.raises(ValueError, match="foreign cached module"):
            load_joined(REPOS)
    for name, module in sys.modules.items():
        prefix = name.split(".")[0]
        if prefix in DEPENDENCIES:
            assert Path(module.__file__).resolve().is_relative_to(REPOS / DEPENDENCIES[prefix])


@pytest.mark.parametrize("case", CASES["numeric_cases"], ids=lambda c: c["id"])
def test_existing_model_known_answers(joined, case):
    result = joined.scenario(seed=case["seed"], **case["input"])
    assert result["actual_allocated_gpus"] == case["expected_allocated_gpus"]
    assert result["count_only_admission_gpus"] == case["expected_count_only_gpus"]
    assert result["buckets"]["useful_compute_gpu_h"] == pytest.approx(case["expected_useful_gpu_h"], abs=1e-6, rel=1e-12)
    assert result["nominal_gpu_h"] == 512 * 720
    assert math.isclose(sum(result["buckets"].values()), 512 * 720, abs_tol=1e-6)
    assert all(math.isfinite(v) and v >= 0 for v in result["buckets"].values())
    allocated = [j for j in result["jobs"] if j["decision"] == "allocated"]
    retained = sum(j["recovery"]["productive_gpu_h"] for j in allocated)
    assert result["buckets"]["recovery_and_discard_gpu_h"] == pytest.approx(len(allocated) * 128 * 720 - retained)
    assert result["buckets"]["useful_compute_gpu_h"] + result["buckets"]["retained_communication_and_bubble_gpu_h"] == pytest.approx(retained)


def test_count_only_counterexample_and_admission_order(joined):
    blocked = joined.scenario()
    # Alternating x planes leave 256 chips, but connected healthy x width is 1;
    # the largest available rectangle is only 1*8*8 = 64 chips.
    assert 4 * 8 * 8 == 256 and 1 * 8 * 8 < 128
    assert {j["decision"] for j in blocked["jobs"]} == {"no-rectangle"}
    healthy = joined.scenario(degraded=False)
    assert [j["decision"] for j in healthy["jobs"]] == ["allocated"] * 3 + ["DENY"]
    # Existing rung arithmetic, independent of allocator output.
    pue = 1.03 + (1 - (.85 + .10)) * .15
    per_gang_mw = 2 * 81.6 / 1000 * pue
    assert per_gang_mw == pytest.approx(.16932)
    assert 3 * per_gang_mw <= .6 < 4 * per_gang_mw


def test_attractive_smaller_gang_rejected_by_existing_memory_model(joined):
    smaller = joined.scenario(gang_size=64)
    assert smaller["actual_allocated_gpus"] > 0
    assert smaller["buckets"]["useful_compute_gpu_h"] > 0
    small = memory_screen(joined, 64)
    normal = memory_screen(joined, 128)
    # Weights/master/moments alone already exceed 80 GB for the 64-way shard.
    assert 405e9 / 64 * 14 / 1e9 == 88.59375
    assert small["estimated_GB_per_device"] == pytest.approx(90.707679216)
    assert small["rough_screen"] == "fail"
    assert normal["estimated_GB_per_device"] == pytest.approx(45.353839608)
    assert normal["rough_screen"] == "pass"


def test_recovery_has_one_owner_and_reproducible_seed(joined):
    result = joined.scenario(degraded=False)
    assert result == joined.scenario(degraded=False)
    rows = [j for j in result["jobs"] if j["decision"] == "allocated"]
    assert rows[0]["recovery"] == rows[1]["recovery"]  # same seed reset, not independent jobs
    r = joined.reliability_module()
    cfg = r.Config(nodes=16, horizon_h=720, single_rate_per_node_h=0, burst_rate_per_h=0, spare_nodes=0)
    no_fault = r.Sim(cfg, "auto-restart", []).run()
    assert no_fault["productive_gpu_h"] == pytest.approx(128 * 720 * .96)
    assert no_fault["waste_gpu_h"] == pytest.approx(128 * 720 * .04)


@pytest.mark.parametrize("case", CASES["semantic_cases"], ids=lambda c: c["id"])
def test_frozen_evidence_meanings(case):
    assert classify(case["checks"], case["required"], comparable=case["comparable"], delta=case["delta_gpu_h"]) == case["expected"]


def test_host_and_freshness_fixture_facts():
    host = CASES["synthetic_host_constraint"]
    assert host["required_GiB"] > host["allocatable_GiB"]
    fresh = PROFILE["freshness"]["synthetic_freshness_tests"]
    now = datetime.fromisoformat(fresh["as_of_utc"])
    for age, expected in [(59, "pass"), (60, "stale"), (61, "stale")]:
        observed = now - timedelta(seconds=age)
        state = "pass" if now < observed + timedelta(seconds=fresh["ttl_s"]) else "stale"
        assert state == expected
    # This TTL tests a rule, not a claimed operator validity window.
    assert PROFILE["freshness"]["operator_dynamic"]["max_age_s"] is None


def test_cooling_known_defect_is_still_present_at_pin(joined):
    rows = cooling_probe()
    assert all(r["verdict"] == "DENY" for r in rows if r["case"].startswith("finite"))
    assert all(r["verdict"] == "ADMIT" for r in rows if r["case"].startswith("nan"))
    # Passing this test documents the defect, not a corrected cooling boundary.


def test_existing_span_missing_and_strict_numeric_boundaries(joined):
    from spancontract.cli import _example_envelope
    from capacity.placement import admission_preview
    result = admission_preview(_example_envelope())
    assert result["not_checked"]
    assert result["passes_supplied_checks"] is False
    assert result["execution_authorized"] is False
    for bad in (math.nan, math.inf, -math.inf, True, "1", None):
        with pytest.raises(ValueError):
            dataclasses.replace(_example_envelope(), power_headroom_kw=bad)


def test_existing_scheduler_comparison_rejects_changed_seed(joined):
    from capacity.report import replay, compare
    baseline = replay({"horizon_days": 1, "seed": 0}, "rigid-fifo")
    changed = replay({"horizon_days": 1, "seed": 1}, "rigid-fifo")
    with pytest.raises(ValueError, match="incomparable"):
        compare(baseline, changed)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, True, "1"])
def test_invalid_delta_cannot_be_used_as_gain(bad):
    with pytest.raises(ValueError):
        classify({"geometry": "pass"}, ["geometry"], delta=bad)


def test_profile_keeps_proof_levels_and_unknowns_separate():
    assert PROFILE["execution_authorized"] is False
    assert PROFILE["action_intent"]["cardinality"] == 1
    assert PROFILE["action_intent"]["mutation_request"] is None
    assert PROFILE["operator"]["scheduler"] is None
    assert PROFILE["cpu_executor_proposal"]["cluster_id"] is None
    assert PROFILE["cpu_executor_proposal"]["execution_authorized"] is False
    assert all(v["required"] is None and v["available"] is None for v in PROFILE["resource_requirements"].values())
    # Strict JSON serialization is required even though Python accepts NaN.
    json.dumps(PROFILE, allow_nan=False)
    assert not any(c["expected"]["mutation_request"] for c in CASES["semantic_cases"])
