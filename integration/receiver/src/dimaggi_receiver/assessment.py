"""Join planning, measured accelerator memory and unresolved source reviews.

The result is an assessment, never a reservation or a grant. Every accelerator
pool selected by the planner needs pinned, attributed telemetry. CPU-only plans
remain usable without fabricated accelerator metrics.
"""

from . import infrastructure as infra
from .jsonio import digest
from .telemetry import memory_screen
from .source_watch import check_queue


def assess(registry, request, registry_digest, evidence, evidence_digest, as_of):
    infra.obj(evidence, ("schema", "telemetry", "source_queue"), "assessment evidence")
    if evidence["schema"] != "dimaggi-assessment-evidence/v1" or digest(
        evidence
    ) != infra.sha(evidence_digest):
        raise ValueError("assessment evidence pin mismatch")
    planned = infra.plan(registry, request, registry_digest, as_of)
    queue = evidence["source_queue"]
    check_queue(queue)
    if (
        not 0
        <= (infra.stamp(as_of) - infra.stamp(queue["observed_at"])).total_seconds()
        < 86400
    ):
        raise ValueError("source review queue stale or future")
    profiles = {p["id"]: p for p in registry["profiles"]}
    pools = {p["id"]: p for p in request["pools"]}
    workloads = {w["id"]: w for w in request["workloads"]}
    blocked = {p for f in queue["findings"] for p in f["affected_profiles"]}
    entries = {}
    for item in infra.array(evidence["telemetry"], "telemetry", nonempty=False):
        infra.obj(
            item,
            ("pool_id", "snapshot", "snapshot_digest", "device_mapping"),
            "telemetry join",
        )
        infra.text(item["pool_id"], "pool_id")
        if item["pool_id"] in entries or item["pool_id"] not in pools:
            raise ValueError("duplicate or foreign telemetry pool")
        entries[item["pool_id"]] = item
    refusals = []
    screens = []
    if planned["status"] != "compatible":
        refusals.append({"reason": "planning_refused"})
    selected = {}
    for allocation in planned["allocations"]:
        selected.setdefault(allocation["pool_id"], []).append(
            workloads[allocation["workload_id"]]
        )
        if allocation["profile_id"] in blocked:
            refusals.append(
                {"pool_id": allocation["pool_id"], "reason": "source_review_pending"}
            )
    for pool_id, jobs in sorted(selected.items()):
        pool = pools[pool_id]
        profile = profiles[pool["profile_id"]]
        if profile["provider"] == "local":
            continue
        if profile["id"] not in queue["profile_ids"]:
            refusals.append(
                {"pool_id": pool_id, "reason": "source_watch_scope_missing"}
            )
        if pool_id not in entries:
            refusals.append({"pool_id": pool_id, "reason": "telemetry_missing"})
            continue
        item = entries[pool_id]
        snapshot = item["snapshot"]
        expected = dict(
            source_id=pool["source_id"],
            source_epoch=pool["epoch"],
            target_id=pool["target_id"],
            observed_at=pool["observed_at"],
            evidence_class=pool["evidence_class"],
        )
        screen = memory_screen(
            snapshot,
            item["snapshot_digest"],
            expected,
            item["device_mapping"],
            max(w["memory_bytes_per_device"] for w in jobs),
            as_of,
            request["freshness_seconds"],
        )
        if snapshot["provider"] != profile["provider"]:
            raise ValueError("provider telemetry mismatch")
        expected_unit = "gpu" if profile["provider"] == "nvidia" else "physical_chip"
        if profile["device_unit"] != expected_unit or profile["partition"] != "whole":
            refusals.append(
                {"pool_id": pool_id, "reason": "partition_mapping_unsupported"}
            )
        if sum(w["resources"]["devices"] for w in jobs) > len(item["device_mapping"]):
            refusals.append(
                {"pool_id": pool_id, "reason": "physical_inventory_insufficient"}
            )
        if profile["provider"] == "nvidia" and any(
            d["driver"] != profile["stack"]["driver"] for d in snapshot["devices"]
        ):
            refusals.append({"pool_id": pool_id, "reason": "driver_mismatch"})
        if screen["status"] != "pass":
            refusals.append({"pool_id": pool_id, "reason": "telemetry_memory_refused"})
        screens.append(dict(pool_id=pool_id, **screen))
    result = dict(
        schema="dimaggi-infrastructure-assessment/v1",
        plan=planned,
        evidence_digest=evidence_digest,
        source_queue_digest=queue["queue_digest"],
        memory_screens=screens,
        unassessed_pool_ids=sorted(set(entries) - {s["pool_id"] for s in screens}),
        status="refused" if refusals else "compatible",
        refusals=refusals,
        execution_authorized=False,
        mutation_request=None,
    )
    result["assessment_digest"] = digest(result)
    return result
