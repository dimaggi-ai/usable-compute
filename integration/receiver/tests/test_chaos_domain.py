"""Exposed deterministic metamorphic checks through the source-locked adapter.

Inputs derive from existing owner fixtures. Transformations are algebraic checks,
not physical demand distributions, independent holdouts, or execution authority.
"""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from dimaggi_receiver.adapters import evaluate, exercise


@pytest.fixture(scope="module")
def owner_cases():
    value = os.environ.get("DIMAGGI_TEST_SOURCES")
    if not value:
        pytest.skip("export locked owner sources and set DIMAGGI_TEST_SOURCES")
    bundle = Path(value)
    response = exercise(bundle)
    assert response["execution_authorized"] is False
    assert response["mutation_request"] is None
    assert response["result"]["all_match"]
    return bundle, {row["case_id"]: row for row in response["result"]["cases"]}


def checked(owner_cases, arguments):
    bundle, _ = owner_cases
    before = deepcopy(arguments)
    response = evaluate(bundle, {"kind": "capacity", "input": arguments})
    assert arguments == before
    assert response["execution_readiness"] == "incomplete"
    assert response["execution_authorized"] is False
    assert response["mutation_request"] is None
    raw = response["result"]["raw"]
    assert raw["execution_authorized"] is False
    assert raw["mutation_request"] is None
    return raw


@pytest.mark.parametrize("case_id", ["host_fits", "cpu_shortfall", "exact_capacity", "shared_storage_overbook"])
def test_disjoint_demand_partition_order_and_scale_preserve_predicate(owner_cases, case_id):
    _, rows = owner_cases
    case = rows["resource/" + case_id]
    original, baseline = case["input"], case["raw"]
    partitioned = deepcopy(original)
    first = next(iter(partitioned["demand_components"]))
    component = partitioned["demand_components"].pop(first)
    half = component["value"] / 2 if original["resource"] == "cpu" else component["value"] // 2
    partitioned["demand_components"][first + "/left"] = dict(component, value=half)
    partitioned["demand_components"][first + "/right"] = dict(component, value=component["value"] - half)
    reordered = deepcopy(partitioned)
    reordered["demand_components"] = dict(reversed(list(reordered["demand_components"].items())))
    for variant in (partitioned, reordered):
        result = checked(owner_cases, variant)
        for field in ("required", "available", "headroom", "check_state", "engineering_readiness"):
            assert result[field] == baseline[field], (case_id, field)
    scaled = deepcopy(original)
    for quantity in [*scaled["demand_components"].values(), scaled["margin"], scaled["available"]]:
        quantity["value"] *= 2
    result = checked(owner_cases, scaled)
    assert result["check_state"] == baseline["check_state"]
    assert result["engineering_readiness"] == baseline["engineering_readiness"]
    for field in ("required", "available", "headroom"):
        assert result[field] == baseline[field] * 2, (case_id, field)


@pytest.mark.parametrize("case_id", ["host_fits", "shared_storage_overbook"])
@pytest.mark.parametrize("offset, expected", [(-1, "fail"), (0, "pass"), (1, "pass")])
def test_one_byte_residual_capacity_crossing(owner_cases, case_id, offset, expected):
    _, rows = owner_cases
    case = rows["resource/" + case_id]
    arguments = deepcopy(case["input"])
    # Use the owner's derived requirement; do not reimplement its arithmetic.
    arguments["available"]["value"] = case["raw"]["required"] + offset
    result = checked(owner_cases, arguments)
    assert result["check_state"] == expected
    assert result["headroom"] == offset
    assert type(result["required"]) is int


@pytest.mark.parametrize("case_id, blocked_state", [
    ("unknown_margin", "missing"),
    ("stale_capacity", "stale"),
    ("not-run-capacity", "not_run"),
])
def test_nominal_resource_changes_cannot_repair_unusable_evidence(owner_cases, case_id, blocked_state):
    _, rows = owner_cases
    arguments = deepcopy(rows["resource/" + case_id]["input"])
    original = checked(owner_cases, arguments)
    # Doubling is an explicit stress transformation, not a sampled workload.
    # For a not_run value, retain the absent number and increase known demands
    # instead; numeric changes cannot repair its missing evaluation state.
    if arguments["available"]["value"] is not None:
        arguments["available"]["value"] *= 2
    else:
        for component in arguments["demand_components"].values():
            component["value"] *= 2
    transformed = checked(owner_cases, arguments)
    assert original["check_state"] == transformed["check_state"] == blocked_state
    assert transformed["engineering_readiness"] == "incomplete"
    assert transformed["required"] is None
    assert transformed["headroom"] is None
