"""Provider-neutral, bounded planning over explicit tuples and measured headroom.

This module grants no authority and never infers compatibility from vendor peaks.
"""

from copy import deepcopy
from datetime import datetime
import re
from .jsonio import canonical, digest, loads

SCHEMA = "dimaggi-infrastructure/v1"
RESOURCES = (
    "devices",
    "cpu_millicores",
    "host_memory_bytes",
    "scratch_bytes",
    "network_bytes_per_second",
    "storage_bytes_per_second",
    "power_watts",
)
STACK = (
    "architecture",
    "os",
    "orchestrator",
    "driver",
    "runtime",
    "framework",
    "device_plugin",
    "network_plugin",
    "collectives",
)
ARCHETYPES = {"training", "inference", "agentic", "cpu_batch"}
EVIDENCE = {"synthetic", "local_lab", "hardware_observed"}


def obj(v, keys, label):
    if type(v) is not dict or set(v) != set(keys):
        raise ValueError(f"{label}: exact fields required: {sorted(keys)}")


def text(v, label):
    if type(v) is not str or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:/+@=-]{0,255}", v
    ):
        raise ValueError(f"{label}: bounded identifier required")


def integer(v, label, minimum=0):
    if type(v) is not int or not minimum <= v <= 2**53 - 1:
        raise ValueError(f"{label}: bounded integer required")
    return v


def stamp(v):
    if type(v) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", v
    ):
        raise ValueError("whole-second UTC Z timestamp required")
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def sha(v):
    if type(v) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", v):
        raise ValueError("SHA-256 reference required")
    return v


def array(v, label, nonempty=True):
    if type(v) is not list or not (1 if nonempty else 0) <= len(v) <= 1024:
        raise ValueError(f"{label}: bounded array required")
    return v


def resources(v):
    obj(v, RESOURCES, "resources")
    for k, x in v.items():
        integer(x, k)


def strings(v, label):
    for x in array(v, label):
        text(x, label)
    if len(set(v)) != len(v):
        raise ValueError(f"{label}: duplicates")


def stack(v):
    obj(v, STACK, "stack")
    for x in v.values():
        text(x, "stack version")


def registry_check(registry, expected_digest):
    registry = loads(canonical(registry).decode())
    if digest(registry) != sha(expected_digest):
        raise ValueError("registry pin mismatch")
    obj(registry, ("schema", "revision", "profiles"), "registry")
    if registry["schema"] != SCHEMA:
        raise ValueError("unsupported registry schema")
    text(registry["revision"], "revision")
    profiles = {}
    for p in array(registry["profiles"], "profiles"):
        obj(
            p,
            (
                "id",
                "provider",
                "generation",
                "device_unit",
                "partition",
                "stack",
                "topologies",
                "archetypes",
                "state",
                "valid_from",
                "valid_until",
                "source_digests",
                "validation_evidence",
                "validation_class",
                "memory_bytes_per_device",
                "precisions",
                "isolation",
            ),
            "profile",
        )
        for k in ("id", "provider", "generation", "device_unit", "partition"):
            text(p[k], k)
        if p["provider"] not in {"local", "google", "nvidia"} or p[
            "device_unit"
        ] not in {"cpu_slot", "physical_chip", "gpu", "mig_instance"}:
            raise ValueError("unsupported provider or device unit")
        if p["state"] not in {"validated", "quarantined", "retired"}:
            raise ValueError("unsupported profile state")
        if stamp(p["valid_until"]) <= stamp(p["valid_from"]):
            raise ValueError("empty profile validity")
        stack(p["stack"])
        for k in ("topologies", "archetypes", "precisions", "isolation"):
            strings(p[k], k)
        if not set(p["archetypes"]) <= ARCHETYPES:
            raise ValueError("unsupported archetype")
        for v in array(p["source_digests"], "source_digests"):
            sha(v)
        sha(p["validation_evidence"])
        if p["validation_class"] not in EVIDENCE:
            raise ValueError("unsupported validation class")
        integer(p["memory_bytes_per_device"], "memory_bytes_per_device", 1)
        if p["id"] in profiles:
            raise ValueError("duplicate profile identity")
        profiles[p["id"]] = p
    return registry, profiles


def plan(registry, request, expected_digest, as_of):
    registry, profiles = registry_check(registry, expected_digest)
    request = loads(canonical(request).decode())
    obj(
        request,
        (
            "schema",
            "request_id",
            "evidence_class",
            "freshness_seconds",
            "pools",
            "workloads",
            "budgets",
        ),
        "request",
    )
    if request["schema"] != SCHEMA or request["evidence_class"] not in EVIDENCE:
        raise ValueError("unsupported request schema or evidence class")
    text(request["request_id"], "request_id")
    freshness = integer(request["freshness_seconds"], "freshness_seconds", 1)
    if freshness > 3600:
        raise ValueError("headroom freshness exceeds one hour")
    now, pools = stamp(as_of), {}
    budgets, stale_budgets = {}, set()
    for b in array(request["budgets"], "budgets", nonempty=False):
        obj(
            b,
            (
                "id",
                "headroom",
                "observed_at",
                "source_id",
                "epoch",
                "evidence_class",
                "evidence_digest",
            ),
            "shared budget",
        )
        text(b["source_id"], "budget source")
        text(b["epoch"], "budget epoch")
        sha(b["evidence_digest"])
        if b["evidence_class"] != request["evidence_class"]:
            raise ValueError("mixed budget evidence class")
        if not 0 <= (now - stamp(b["observed_at"])).total_seconds() < freshness:
            stale_budgets.add(b["id"])
        text(b["id"], "budget id")
        if b["id"] in budgets:
            raise ValueError("duplicate shared budget")
        if (
            type(b["headroom"]) is not dict
            or not b["headroom"]
            or not set(b["headroom"]) <= set(RESOURCES)
        ):
            raise ValueError("shared budget requires known resource dimensions")
        for k, v in b["headroom"].items():
            integer(v, k)
        budgets[b["id"]] = deepcopy(b["headroom"])
    for p in array(request["pools"], "pools"):
        obj(
            p,
            (
                "id",
                "profile_id",
                "target_id",
                "namespace_uid",
                "source_id",
                "epoch",
                "observed_at",
                "evidence_class",
                "evidence_digest",
                "stack",
                "topology",
                "device_unit",
                "partition",
                "headroom",
                "latency_p99_us",
                "checkpoint_restore_seconds",
                "healthy",
                "isolation",
                "budget_ids",
            ),
            "pool",
        )
        for k in (
            "id",
            "profile_id",
            "target_id",
            "namespace_uid",
            "source_id",
            "epoch",
            "topology",
            "device_unit",
            "partition",
            "isolation",
        ):
            text(p[k], k)
        if p["id"] in pools:
            raise ValueError("duplicate pool identity")
        if p["evidence_class"] != request["evidence_class"]:
            raise ValueError("mixed evidence classes")
        sha(p["evidence_digest"])
        stamp(p["observed_at"])
        resources(p["headroom"])
        stack(p["stack"])
        integer(p["latency_p99_us"], "latency_p99_us")
        integer(p["checkpoint_restore_seconds"], "checkpoint_restore_seconds")
        if type(p["healthy"]) is not bool:
            raise ValueError("healthy must be boolean")
        for bid in array(p["budget_ids"], "budget_ids", nonempty=False):
            if type(bid) is not str or bid not in budgets:
                raise ValueError("unknown shared budget")
        if len(set(p["budget_ids"])) != len(p["budget_ids"]):
            raise ValueError("duplicate pool budget")
        pools[p["id"]] = p
    if len({p["target_id"] for p in pools.values()}) != len(pools):
        raise ValueError("overlapping target pools are unsupported")
    if len(pools) * len(array(request["workloads"], "workloads")) > 4096:
        raise ValueError("planning comparison budget exceeded")
    remaining = {k: deepcopy(v["headroom"]) for k, v in pools.items()}
    budget_remaining = deepcopy(budgets)
    allocations, refusals, ids = [], [], set()
    for w in array(request["workloads"], "workloads"):
        obj(
            w,
            (
                "id",
                "archetype",
                "allowed_profiles",
                "resources",
                "memory_bytes_per_device",
                "precision",
                "isolation",
                "allowed_topologies",
                "max_latency_p99_us",
                "max_restore_seconds",
                "allow_fallback",
                "desired_state",
            ),
            "workload",
        )
        text(w["id"], "workload id")
        if w["desired_state"] not in {"running", "succeeded"}:
            raise ValueError("explicit desired workload state required")
        if w["id"] in ids:
            raise ValueError("duplicate workload identity")
        ids.add(w["id"])
        if w["archetype"] not in ARCHETYPES:
            raise ValueError("unsupported workload archetype")
        for k in ("allowed_profiles", "allowed_topologies"):
            strings(w[k], k)
        if type(w["allow_fallback"]) is not bool:
            raise ValueError("allow_fallback must be boolean")
        resources(w["resources"])
        integer(w["resources"]["devices"], "devices", 1)
        for k in (
            "memory_bytes_per_device",
            "max_latency_p99_us",
            "max_restore_seconds",
        ):
            integer(w[k], k, 1)
        for k in ("precision", "isolation"):
            text(w[k], k)
        candidates, rejected = [], []
        for pool_id, p in sorted(pools.items()):
            profile, reasons = profiles.get(p["profile_id"]), []
            if profile is None:
                reasons.append("unknown_profile")
            else:
                tests = (
                    (profile["state"] == "validated", "profile_not_validated"),
                    (
                        profile["validation_class"] == p["evidence_class"],
                        "validation_evidence_class_mismatch",
                    ),
                    (
                        stamp(profile["valid_from"])
                        <= now
                        < stamp(profile["valid_until"]),
                        "profile_expired_or_future",
                    ),
                    (w["archetype"] in profile["archetypes"], "archetype_unsupported"),
                    (w["precision"] in profile["precisions"], "precision_unsupported"),
                    (
                        w["memory_bytes_per_device"]
                        <= profile["memory_bytes_per_device"],
                        "device_memory_insufficient",
                    ),
                    (p["stack"] == profile["stack"], "stack_mismatch"),
                    (
                        p["device_unit"] == profile["device_unit"]
                        and p["partition"] == profile["partition"],
                        "device_identity_mismatch",
                    ),
                    (p["topology"] in profile["topologies"], "topology_unvalidated"),
                    (w["isolation"] in profile["isolation"], "isolation_unvalidated"),
                )
                reasons.extend(reason for ok, reason in tests if not ok)
            allowed = (
                w["allowed_profiles"]
                if w["allow_fallback"]
                else w["allowed_profiles"][:1]
            )
            tests = (
                (p["profile_id"] in allowed, "profile_not_allowed"),
                (
                    0 <= (now - stamp(p["observed_at"])).total_seconds() < freshness,
                    "stale_or_future_headroom",
                ),
                (p["healthy"], "unhealthy"),
                (p["isolation"] == w["isolation"], "isolation_mismatch"),
                (p["topology"] in w["allowed_topologies"], "topology_not_allowed"),
                (p["latency_p99_us"] <= w["max_latency_p99_us"], "latency_slo"),
                (
                    p["checkpoint_restore_seconds"] <= w["max_restore_seconds"],
                    "recovery_slo",
                ),
            )
            reasons.extend(reason for ok, reason in tests if not ok)
            reasons.extend(
                "insufficient_" + k
                for k in RESOURCES
                if w["resources"][k] > remaining[pool_id][k]
            )
            for bid in p["budget_ids"]:
                if bid in stale_budgets:
                    reasons.append("stale_or_future_budget:" + bid)
                reasons.extend(
                    "shared_budget:" + bid + ":" + k
                    for k, v in budget_remaining[bid].items()
                    if w["resources"][k] > v
                )
            if reasons:
                rejected.append({"pool_id": pool_id, "reasons": reasons})
            else:
                candidates.append(
                    (
                        allowed.index(p["profile_id"]),
                        remaining[pool_id]["devices"] - w["resources"]["devices"],
                        pool_id,
                    )
                )
        if not candidates:
            refusals.append({"workload_id": w["id"], "candidates": rejected})
            continue
        pool_id = min(candidates)[2]
        p = pools[pool_id]
        for k in RESOURCES:
            remaining[pool_id][k] -= w["resources"][k]
        for bid in p["budget_ids"]:
            for k in budget_remaining[bid]:
                budget_remaining[bid][k] -= w["resources"][k]
        allocations.append(
            dict(
                workload_id=w["id"],
                pool_id=pool_id,
                profile_id=p["profile_id"],
                target_id=p["target_id"],
                namespace_uid=p["namespace_uid"],
                source_id=p["source_id"],
                epoch=p["epoch"],
                resources=w["resources"],
                pool_evidence_digest=p["evidence_digest"],
            )
        )
    result = dict(
        schema="dimaggi-infrastructure-plan/v1",
        request_id=request["request_id"],
        registry_digest=expected_digest,
        input_digest=digest(request),
        as_of=as_of,
        evidence_class=request["evidence_class"],
        status="refused" if refusals else "compatible",
        allocations=[] if refusals else allocations,
        refusals=refusals,
        remaining=(
            {k: v["headroom"] for k, v in pools.items()} if refusals else remaining
        ),
        budget_remaining=budgets if refusals else budget_remaining,
        execution_authorized=False,
        mutation_request=None,
    )
    result["plan_digest"] = digest(result)
    return result


def reconcile(
    registry, request, expected_digest, planned, observations, expected_objects, as_of
):
    expected = plan(registry, request, expected_digest, planned["as_of"])
    if planned != expected or planned["status"] != "compatible":
        raise ValueError("a reproducible compatible plan is required")
    current = plan(registry, request, expected_digest, as_of)
    obj(observations, ("schema", "plan_digest", "items"), "observations")
    if (
        observations["schema"] != "dimaggi-infrastructure-observations/v1"
        or observations["plan_digest"] != planned["plan_digest"]
    ):
        raise ValueError("observation plan binding mismatch")
    obj(
        expected_objects,
        [a["workload_id"] for a in planned["allocations"]],
        "expected object identities",
    )
    for v in expected_objects.values():
        text(v, "expected object UID")
    now, found, conditions = stamp(as_of), {}, []
    if now < stamp(planned["as_of"]):
        raise ValueError("reconciliation clock rollback")
    if current["status"] != "compatible":
        conditions.append("planning_evidence_no_longer_valid")
    for item in array(observations["items"], "observations", nonempty=False):
        obj(
            item,
            (
                "workload_id",
                "source_id",
                "epoch",
                "target_id",
                "namespace_uid",
                "object_uid",
                "observed_at",
                "evidence_class",
                "profile_id",
                "stack",
                "topology",
                "device_unit",
                "partition",
                "resources",
                "state",
                "evidence_digest",
            ),
            "observation",
        )
        for k in (
            "workload_id",
            "source_id",
            "epoch",
            "target_id",
            "namespace_uid",
            "object_uid",
            "profile_id",
            "topology",
            "device_unit",
            "partition",
        ):
            text(item[k], k)
        if item["workload_id"] in found:
            raise ValueError("duplicate workload observation")
        sha(item["evidence_digest"])
        stamp(item["observed_at"])
        resources(item["resources"])
        stack(item["stack"])
        if item["state"] not in {"running", "succeeded", "failed", "unknown"}:
            raise ValueError("unsupported workload state")
        found[item["workload_id"]] = item
    pools = {p["id"]: p for p in request["pools"]}
    desired = {w["id"]: w["desired_state"] for w in request["workloads"]}
    if set(found) - {a["workload_id"] for a in planned["allocations"]}:
        conditions.append("foreign_workload")
    if len({i["object_uid"] for i in found.values()}) != len(found):
        conditions.append("object_identity_reuse")
    for a in planned["allocations"]:
        w = a["workload_id"]
        item = found.get(w)
        if item is None:
            conditions.append(w + ":missing")
            continue
        p = pools[a["pool_id"]]
        checks = {
            k: a[k]
            for k in (
                "source_id",
                "epoch",
                "target_id",
                "namespace_uid",
                "profile_id",
                "resources",
            )
        }
        checks.update(
            {k: p[k] for k in ("stack", "topology", "device_unit", "partition")}
        )
        checks["evidence_class"] = planned["evidence_class"]
        checks["object_uid"] = expected_objects[w]
        conditions.extend(
            w + ":mismatch_" + k for k, v in checks.items() if item[k] != v
        )
        if (
            not 0
            <= (now - stamp(item["observed_at"])).total_seconds()
            < request["freshness_seconds"]
        ):
            conditions.append(w + ":stale_or_future_observation")
        if stamp(item["observed_at"]) < stamp(planned["as_of"]):
            conditions.append(w + ":observation_predates_plan")
        if item["state"] != desired[w]:
            conditions.append(w + ":" + item["state"])
    result = dict(
        schema="dimaggi-infrastructure-reconciliation/v1",
        plan_digest=planned["plan_digest"],
        observations_digest=digest(observations),
        expected_objects_digest=digest(expected_objects),
        as_of=as_of,
        evidence_class=planned["evidence_class"],
        status="diverged" if conditions else "consistent",
        conditions=sorted(conditions),
        execution_authorized=False,
        mutation_request=None,
    )
    result["receipt_digest"] = digest(result)
    return result


def release_diff(current, candidate, current_digest, candidate_digest):
    _, before = registry_check(current, current_digest)
    _, after = registry_check(candidate, candidate_digest)
    changes = [
        dict(
            profile_id=k,
            kind=(
                "added"
                if k not in before
                else "removed" if k not in after else "changed"
            ),
            required_state="quarantined",
            before_digest=digest(before[k]) if k in before else None,
            after_digest=digest(after[k]) if k in after else None,
        )
        for k in sorted(before.keys() | after.keys())
        if before.get(k) != after.get(k)
    ]
    return dict(
        schema="dimaggi-infrastructure-drift/v1",
        current_digest=current_digest,
        candidate_digest=candidate_digest,
        changes=changes,
        activation_authorized=False,
        execution_authorized=False,
        mutation_request=None,
    )


def cpu_binding(registry, request, expected_digest, planned, as_of):
    """Headroom evidence for TENWA's existing signed Deployment configuration.

    The deployment owner separately approves evidence trust, image, isolation,
    namespace protections and authority. This projection cannot set those flags.
    """
    from datetime import timedelta

    if (
        planned != plan(registry, request, expected_digest, as_of)
        or planned["status"] != "compatible"
    ):
        raise ValueError("a current reproducible compatible plan is required")
    if len(planned["allocations"]) != 1:
        raise ValueError("CPU binding requires exactly one workload")
    a = planned["allocations"][0]
    w = request["workloads"][0]
    p = next(p for p in request["pools"] if p["id"] == a["pool_id"])
    profile = next(p for p in registry["profiles"] if p["id"] == a["profile_id"])
    if (
        w["archetype"] != "cpu_batch"
        or profile["provider"] != "local"
        or p["device_unit"] != "cpu_slot"
        or p["partition"] != "whole"
        or p["topology"] != "single-host"
    ):
        raise ValueError("only local whole-slot single-host CPU execution is supported")
    if profile["stack"]["architecture"] not in {"arm64", "amd64"}:
        raise ValueError("unsupported CPU architecture")
    r = a["resources"]
    if (
        r["devices"] != 1
        or not 1 <= r["cpu_millicores"] <= 1000
        or not 1 <= r["host_memory_bytes"] <= 256 << 20
        or not 1 <= r["scratch_bytes"] <= 64 << 20
    ):
        raise ValueError("CPU binding exceeds the bounded executor contract")
    expiry = min(
        stamp(profile["valid_until"]),
        stamp(p["observed_at"]) + timedelta(seconds=request["freshness_seconds"]),
        *(
            stamp(b["observed_at"]) + timedelta(seconds=request["freshness_seconds"])
            for b in request["budgets"]
            if b["id"] in p["budget_ids"]
        ),
    )
    return dict(
        schema="dimaggi-infrastructure-cpu-binding/v1",
        plan_digest=planned["plan_digest"],
        registry_digest=expected_digest,
        input_digest=planned["input_digest"],
        request_id=request["request_id"],
        workload_id=w["id"],
        cluster_id=p["target_id"],
        namespace_uid=p["namespace_uid"],
        architecture=p["stack"]["architecture"],
        cpu_millicores=r["cpu_millicores"],
        memory_bytes=r["host_memory_bytes"],
        ephemeral_storage_bytes=r["scratch_bytes"],
        valid_from=as_of,
        valid_until=expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        evidence_class=request["evidence_class"],
        permission="not_granted",
        execution_authorized=False,
    )
