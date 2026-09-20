"""Generate explicitly synthetic contracts; values are test inputs, not vendor specs."""

from pathlib import Path
from dimaggi_receiver.infrastructure import (
    SCHEMA,
    RESOURCES,
    plan,
    reconcile,
    cpu_binding,
)
from dimaggi_receiver.jsonio import digest, dumps


def fixtures():
    stack = dict(
        architecture="arm64",
        os="linux",
        orchestrator="kubernetes-1.35.0",
        driver="none",
        runtime="test-runtime-1",
        framework="cpu-payload-1",
        device_plugin="none",
        network_plugin="test-cni-1",
        collectives="none",
    )
    profile = dict(
        id="local.cpu.test-v1",
        provider="local",
        generation="cpu-test",
        device_unit="cpu_slot",
        partition="whole",
        stack=stack,
        topologies=["single-host"],
        archetypes=["cpu_batch"],
        state="validated",
        valid_from="2026-09-20T00:00:00Z",
        valid_until="2026-09-21T00:00:00Z",
        source_digests=[digest("synthetic source")],
        validation_evidence=digest("synthetic validation"),
        validation_class="synthetic",
        memory_bytes_per_device=268435456,
        precisions=["int64"],
        isolation=["dedicated-namespace"],
    )
    registry = dict(schema=SCHEMA, revision="synthetic-1", profiles=[profile])
    pool = dict(
        id="pool-1",
        profile_id=profile["id"],
        target_id="synthetic-cluster",
        namespace_uid="synthetic-ns-uid",
        source_id="synthetic-collector",
        epoch="epoch-1",
        observed_at="2026-09-20T12:00:00Z",
        evidence_class="synthetic",
        evidence_digest=digest("synthetic headroom"),
        stack=stack,
        topology="single-host",
        device_unit="cpu_slot",
        partition="whole",
        headroom=dict(
            zip(RESOURCES, [4, 1000, 268435456, 67108864, 1000000000, 100000000, 200])
        ),
        latency_p99_us=100,
        checkpoint_restore_seconds=2,
        healthy=True,
        isolation="dedicated-namespace",
        budget_ids=["site-feed"],
    )
    workload = dict(
        id="payload-1",
        archetype="cpu_batch",
        allowed_profiles=[profile["id"]],
        resources=dict(zip(RESOURCES, [1, 500, 67108864, 16777216, 0, 1000, 20])),
        memory_bytes_per_device=67108864,
        precision="int64",
        isolation="dedicated-namespace",
        allowed_topologies=["single-host"],
        max_latency_p99_us=1000,
        max_restore_seconds=10,
        allow_fallback=False,
        desired_state="succeeded",
    )
    request = dict(
        schema=SCHEMA,
        request_id="infra-cpu-1",
        evidence_class="synthetic",
        freshness_seconds=300,
        pools=[pool],
        workloads=[workload],
        budgets=[
            {
                "id": "site-feed",
                "headroom": {"power_watts": 200},
                "observed_at": pool["observed_at"],
                "source_id": pool["source_id"],
                "epoch": pool["epoch"],
                "evidence_class": "synthetic",
                "evidence_digest": digest("synthetic shared feed"),
            }
        ],
    )
    return registry, request


def observations(request, planned):
    p = request["pools"][0]
    a = planned["allocations"][0]
    item = {
        k: p[k]
        for k in (
            "source_id",
            "epoch",
            "target_id",
            "namespace_uid",
            "evidence_class",
            "profile_id",
            "stack",
            "topology",
            "device_unit",
            "partition",
        )
    }
    item.update(
        workload_id=a["workload_id"],
        object_uid="synthetic-object-1",
        observed_at=planned["as_of"],
        resources=a["resources"],
        state="succeeded",
        evidence_digest=digest("synthetic outcome"),
    )
    return dict(
        schema="dimaggi-infrastructure-observations/v1",
        plan_digest=planned["plan_digest"],
        items=[item],
    )


if __name__ == "__main__":
    r, q = fixtures()
    pin = digest(r)
    now = "2026-09-20T12:00:01Z"
    p = plan(r, q, pin, now)
    o = observations(q, p)
    expected = {"payload-1": "synthetic-object-1"}
    result = reconcile(r, q, pin, p, o, expected, now)
    for name, value in dict(
        registry=r,
        request=q,
        plan=p,
        observations=o,
        expected_objects=expected,
        reconciliation=result,
        cpu_binding=cpu_binding(r, q, pin, p, now),
        pins={"registry_digest": pin, "as_of": now},
    ).items():
        Path(__file__).with_name(name + ".json").write_text(dumps(value))
