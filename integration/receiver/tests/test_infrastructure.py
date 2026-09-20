"""Known-answer, conservation, adverse-boundary and metamorphic checks."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import random
import pytest
from dimaggi_receiver.infrastructure import (
    plan,
    reconcile,
    release_diff,
    cpu_binding,
    RESOURCES,
)
from dimaggi_receiver.jsonio import digest, loads

spec = importlib.util.spec_from_file_location(
    "infra_fixture", Path(__file__).parents[1] / "examples/infrastructure/generate.py"
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
NOW = "2026-09-20T12:00:01Z"


def run(r, q):
    return plan(r, q, digest(r), NOW)


def test_known_answer_and_no_input_mutation():
    r, q = fixture.fixtures()
    original = deepcopy((r, q))
    p = run(r, q)
    assert (r, q) == original
    assert p["status"] == "compatible" and len(p["allocations"]) == 1
    assert p["remaining"]["pool-1"]["cpu_millicores"] == 500
    assert p["execution_authorized"] is False and p["mutation_request"] is None
    assert p == run(r, q)
    o = fixture.observations(q, p)
    result = reconcile(r, q, digest(r), p, o, {"payload-1": "synthetic-object-1"}, NOW)
    assert result["status"] == "consistent" and result["evidence_class"] == "synthetic"


@pytest.mark.parametrize("key", RESOURCES)
def test_each_shared_resource_conserved_and_atomic(key):
    r, q = fixture.fixtures()
    w = q["workloads"][0]
    w["resources"][key] = 1
    q["pools"][0]["headroom"][key] = 1
    second = deepcopy(w)
    second["id"] = "second"
    q["workloads"].append(second)
    p = run(r, q)
    assert p["status"] == "refused" and not p["allocations"]
    assert p["remaining"]["pool-1"] == q["pools"][0]["headroom"]
    assert "insufficient_" + key in p["refusals"][0]["candidates"][0]["reasons"]


@pytest.mark.parametrize(
    "case,reason",
    [
        ("unknown", "unknown_profile"),
        ("quarantine", "profile_not_validated"),
        ("retired", "profile_not_validated"),
        ("profile_expired", "profile_expired_or_future"),
        ("profile_future", "profile_expired_or_future"),
        ("precision", "precision_unsupported"),
        ("memory", "device_memory_insufficient"),
        ("driver", "stack_mismatch"),
        ("collective", "stack_mismatch"),
        ("unit", "device_identity_mismatch"),
        ("partition", "device_identity_mismatch"),
        ("topology", "topology_unvalidated"),
        ("stale", "stale_or_future_headroom"),
        ("future", "stale_or_future_headroom"),
        ("unhealthy", "unhealthy"),
        ("latency", "latency_slo"),
        ("recovery", "recovery_slo"),
        ("archetype", "archetype_unsupported"),
        ("isolation", "isolation_mismatch"),
        ("validation_class", "validation_evidence_class_mismatch"),
    ],
)
def test_fault_hooks(case, reason):
    r, q = fixture.fixtures()
    p = q["pools"][0]
    w = q["workloads"][0]
    profile = r["profiles"][0]
    if case == "unknown":
        p["profile_id"] = "google.tpu.v10.unknown"
    elif case == "quarantine":
        profile["state"] = "quarantined"
    elif case == "retired":
        profile["state"] = "retired"
    elif case == "profile_expired":
        profile["valid_until"] = "2026-09-20T12:00:01Z"
    elif case == "profile_future":
        profile["valid_from"] = "2026-09-20T12:00:02Z"
    elif case == "precision":
        w["precision"] = "fp8"
    elif case == "memory":
        w["memory_bytes_per_device"] = profile["memory_bytes_per_device"] + 1
    elif case == "driver":
        p["stack"] = dict(p["stack"], driver="unvalidated")
    elif case == "collective":
        p["stack"] = dict(p["stack"], collectives="unvalidated")
    elif case == "unit":
        p["device_unit"] = "tensorcore"
    elif case == "partition":
        p["partition"] = "mig-test"
    elif case == "topology":
        p["topology"] = "split-rack"
    elif case == "stale":
        p["observed_at"] = "2026-09-20T11:55:01Z"
    elif case == "future":
        p["observed_at"] = "2026-09-20T12:00:02Z"
    elif case == "unhealthy":
        p["healthy"] = False
    elif case == "latency":
        p["latency_p99_us"] = w["max_latency_p99_us"] + 1
    elif case == "recovery":
        p["checkpoint_restore_seconds"] = w["max_restore_seconds"] + 1
    elif case == "archetype":
        w["archetype"] = "agentic"
    elif case == "isolation":
        p["isolation"] = "shared"
    elif case == "validation_class":
        profile["validation_class"] = "hardware_observed"
    result = run(r, q)
    assert result["status"] == "refused"
    assert reason in result["refusals"][0]["candidates"][0]["reasons"]


@pytest.mark.parametrize(
    "value", [True, -1, 1.5, "100", None, float("nan"), float("inf"), 2**53]
)
@pytest.mark.parametrize("key", RESOURCES)
def test_numeric_boundaries(key, value):
    r, q = fixture.fixtures()
    q["pools"][0]["headroom"][key] = value
    with pytest.raises(ValueError):
        run(r, q)


def test_duplicate_unknown_missing_and_pins():
    r, q = fixture.fixtures()
    with pytest.raises(ValueError):
        plan(r, q, digest("wrong"), NOW)
    for field in list(q):
        mutated = deepcopy(q)
        del mutated[field]
        with pytest.raises(ValueError):
            run(r, mutated)
    q["extra"] = False
    with pytest.raises(ValueError):
        run(r, q)
    r, q = fixture.fixtures()
    q["pools"].append(deepcopy(q["pools"][0]))
    with pytest.raises(ValueError):
        run(r, q)
    q["pools"][1]["id"] = "pool-2"
    with pytest.raises(ValueError):
        run(r, q)  # same target aliases capacity
    with pytest.raises(ValueError):
        loads('{"schema":"x","schema":"y"}')


def test_fallback_requires_explicit_permission():
    r, q = fixture.fixtures()
    q["workloads"][0]["allowed_profiles"].insert(0, "absent-preferred")
    assert run(r, q)["status"] == "refused"
    q["workloads"][0]["allow_fallback"] = True
    assert run(r, q)["status"] == "compatible"


@pytest.mark.parametrize(
    "provider,unit,archetype",
    [
        ("google", "physical_chip", "training"),
        ("nvidia", "gpu", "inference"),
        ("nvidia", "mig_instance", "agentic"),
    ],
)
def test_provider_neutral_archetypes(provider, unit, archetype):
    r, q = fixture.fixtures()
    p = r["profiles"][0]
    p.update(provider=provider, device_unit=unit, archetypes=[archetype])
    q["pools"][0]["device_unit"] = unit
    q["workloads"][0]["archetype"] = archetype
    result = run(r, q)
    assert result["status"] == "compatible"
    with pytest.raises(ValueError):
        cpu_binding(r, q, digest(r), result, NOW)


@pytest.mark.parametrize(
    "key",
    [
        "source_id",
        "epoch",
        "target_id",
        "namespace_uid",
        "profile_id",
        "object_uid",
        "device_unit",
        "partition",
        "topology",
        "evidence_class",
    ],
)
def test_observation_identity_drift(key):
    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    o["items"][0][key] = "foreign"
    result = reconcile(r, q, digest(r), p, o, {"payload-1": "synthetic-object-1"}, NOW)
    assert (
        result["status"] == "diverged"
        and "payload-1:mismatch_" + key in result["conditions"]
    )


def test_missing_stale_failed_observations_and_tampering():
    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    expected = {"payload-1": "synthetic-object-1"}
    o["items"] = []
    assert reconcile(r, q, digest(r), p, o, expected, NOW)["conditions"] == [
        "payload-1:missing"
    ]
    o = fixture.observations(q, p)
    o["items"][0]["state"] = "failed"
    assert (
        "payload-1:failed"
        in reconcile(r, q, digest(r), p, o, expected, NOW)["conditions"]
    )
    o["items"][0]["observed_at"] = "2026-09-20T11:00:00Z"
    assert (
        "payload-1:stale_or_future_observation"
        in reconcile(r, q, digest(r), p, o, expected, NOW)["conditions"]
    )
    p["allocations"][0]["resources"]["cpu_millicores"] = 1
    with pytest.raises(ValueError):
        reconcile(r, q, digest(r), p, o, expected, NOW)


def test_expiry_drift_and_no_auto_activation():
    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    result = reconcile(
        r,
        q,
        digest(r),
        p,
        o,
        {"payload-1": "synthetic-object-1"},
        "2026-09-20T12:05:00Z",
    )
    assert "planning_evidence_no_longer_valid" in result["conditions"]
    candidate = deepcopy(r)
    candidate["profiles"][0]["stack"]["driver"] = "next"
    result = release_diff(r, candidate, digest(r), digest(candidate))
    assert (
        len(result["changes"]) == 1
        and result["changes"][0]["required_state"] == "quarantined"
    )
    assert result["activation_authorized"] is False
    assert not release_diff(r, r, digest(r), digest(r))["changes"]


def test_cpu_binding_scope_and_expiry():
    r, q = fixture.fixtures()
    p = run(r, q)
    b = cpu_binding(r, q, digest(r), p, NOW)
    assert b["valid_until"] == "2026-09-20T12:05:00Z"
    assert b["cpu_millicores"] == 500 and b["permission"] == "not_granted"
    with pytest.raises(ValueError):
        cpu_binding(r, q, digest(r), p, "2026-09-20T12:00:02Z")


def test_seeded_capacity_monotonicity_and_conservation():
    rng = random.Random(20260920)
    for _ in range(100):
        r, q = fixture.fixtures()
        w = q["workloads"][0]
        pool = q["pools"][0]
        for k in RESOURCES:
            w["resources"][k] = rng.randint(1, 10)
            pool["headroom"][k] = rng.randint(0, 20)
        result = run(r, q)
        fits = all(w["resources"][k] <= pool["headroom"][k] for k in RESOURCES)
        assert (result["status"] == "compatible") == fits
        if fits:
            for k in RESOURCES:
                assert (
                    result["remaining"]["pool-1"][k] + w["resources"][k]
                    == pool["headroom"][k]
                )
        for k in RESOURCES:
            pool["headroom"][k] += 10
        assert run(r, q)["status"] == "compatible"


def test_shared_power_feed_between_distinct_pools():
    r, q = fixture.fixtures()
    q["budgets"][0]["headroom"]["power_watts"] = 30
    q["pools"][0]["headroom"]["devices"] = 1
    p2 = deepcopy(q["pools"][0])
    p2.update(id="pool-2", target_id="second-host")
    q["pools"].append(p2)
    w2 = deepcopy(q["workloads"][0])
    w2["id"] = "payload-2"
    q["workloads"].append(w2)
    result = run(r, q)
    assert result["status"] == "refused" and not result["allocations"]
    assert result["budget_remaining"]["site-feed"]["power_watts"] == 30
    assert any(
        "shared_budget:site-feed:power_watts" in p["reasons"]
        for p in result["refusals"][0]["candidates"]
    )
    q["budgets"][0]["headroom"]["power_watts"] = 40
    result = run(r, q)
    assert (
        result["status"] == "compatible"
        and result["budget_remaining"]["site-feed"]["power_watts"] == 0
    )


def test_shared_budget_must_exist_and_be_bounded():
    r, q = fixture.fixtures()
    q["pools"][0]["budget_ids"] = ["unknown"]
    with pytest.raises(ValueError):
        run(r, q)
    q["pools"][0]["budget_ids"] = ["site-feed"]
    q["budgets"][0]["headroom"]["watts"] = 100
    with pytest.raises(ValueError):
        run(r, q)


def test_stale_shared_budget_refuses_and_limits_binding_expiry():
    r, q = fixture.fixtures()
    q["budgets"][0]["observed_at"] = "2026-09-20T11:55:01Z"
    assert run(r, q)["status"] == "refused"
    q["budgets"][0]["observed_at"] = "2026-09-20T11:59:00Z"
    p = run(r, q)
    b = cpu_binding(r, q, digest(r), p, NOW)
    assert b["valid_until"] == "2026-09-20T12:04:00Z"


def test_installed_cli_exact_example_commands(tmp_path):
    import json
    import subprocess
    import sys
    from dimaggi_receiver.jsonio import dumps

    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    for name, value in dict(
        registry=r,
        request=q,
        plan=p,
        observations=o,
        expected={"payload-1": "synthetic-object-1"},
    ).items():
        (tmp_path / (name + ".json")).write_text(dumps(value))
    common = [
        "--registry",
        str(tmp_path / "registry.json"),
        "--registry-digest",
        digest(r),
        "--input",
        str(tmp_path / "request.json"),
        "--as-of",
        NOW,
    ]
    commands = [
        ("infrastructure-plan", common, "status", "compatible"),
        (
            "infrastructure-reconcile",
            common
            + [
                "--plan",
                str(tmp_path / "plan.json"),
                "--observations",
                str(tmp_path / "observations.json"),
                "--expected-objects",
                str(tmp_path / "expected.json"),
            ],
            "status",
            "consistent",
        ),
        (
            "infrastructure-cpu-binding",
            common + ["--plan", str(tmp_path / "plan.json")],
            "permission",
            "not_granted",
        ),
        (
            "infrastructure-drift",
            [
                "--registry",
                str(tmp_path / "registry.json"),
                "--registry-digest",
                digest(r),
                "--candidate",
                str(tmp_path / "registry.json"),
                "--candidate-digest",
                digest(r),
            ],
            "changes",
            [],
        ),
    ]
    for cmd, args, key, expected in commands:
        result = subprocess.run(
            [sys.executable, "-m", "dimaggi_receiver.cli", cmd, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)[key] == expected
    (tmp_path / "request.json").write_text('{"schema":"a","schema":"b"}')
    result = subprocess.run(
        [sys.executable, "-m", "dimaggi_receiver.cli", "infrastructure-plan", *common],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (
        result.returncode == 2 and not json.loads(result.stdout)["execution_authorized"]
    )


def test_comparison_budget_refuses_before_placement():
    r, q = fixture.fixtures()
    base_pool = q["pools"][0]
    base_work = q["workloads"][0]
    q["pools"] = [
        dict(deepcopy(base_pool), id="pool-" + str(i), target_id="host-" + str(i))
        for i in range(65)
    ]
    q["workloads"] = [dict(deepcopy(base_work), id="work-" + str(i)) for i in range(65)]
    with pytest.raises(ValueError, match="comparison budget"):
        run(r, q)


def test_foreign_duplicate_observations_and_changed_plan_refuse():
    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    expected = {"payload-1": "synthetic-object-1"}
    extra = deepcopy(o["items"][0])
    extra.update(workload_id="foreign", object_uid="another-object")
    o["items"].append(extra)
    assert (
        "foreign_workload"
        in reconcile(r, q, digest(r), p, o, expected, NOW)["conditions"]
    )
    o["items"][1] = deepcopy(o["items"][0])
    with pytest.raises(ValueError, match="duplicate"):
        reconcile(r, q, digest(r), p, o, expected, NOW)
    o = fixture.observations(q, p)
    with pytest.raises(ValueError, match="rollback"):
        reconcile(r, q, digest(r), p, o, expected, "2026-09-20T12:00:00Z")
    p["execution_authorized"] = True
    with pytest.raises(ValueError, match="reproducible"):
        reconcile(r, q, digest(r), p, o, expected, NOW)


def test_running_does_not_satisfy_requested_completion():
    r, q = fixture.fixtures()
    p = run(r, q)
    o = fixture.observations(q, p)
    o["items"][0]["state"] = "running"
    result = reconcile(r, q, digest(r), p, o, {"payload-1": "synthetic-object-1"}, NOW)
    assert result["status"] == "diverged" and result["conditions"] == [
        "payload-1:running"
    ]
    q["workloads"][0]["desired_state"] = "running"
    p = run(r, q)
    o = fixture.observations(q, p)
    o["items"][0]["state"] = "running"
    assert (
        reconcile(r, q, digest(r), p, o, {"payload-1": "synthetic-object-1"}, NOW)[
            "status"
        ]
        == "consistent"
    )
