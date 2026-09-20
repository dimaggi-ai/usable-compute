"""Bounded synthetic scaling measurement; no distributed-performance claim."""

import json
import statistics
import time
from copy import deepcopy
from generate import fixtures
from dimaggi_receiver.infrastructure import plan
from dimaggi_receiver.jsonio import digest

results = []
for size in (1, 8, 32, 64):
    r, q = fixtures()
    q["budgets"][0]["headroom"]["power_watts"] = size * 20
    p = q["pools"][0]
    w = q["workloads"][0]
    p["headroom"]["devices"] = 1
    q["pools"] = [
        dict(deepcopy(p), id="pool-" + str(i), target_id="host-" + str(i))
        for i in range(size)
    ]
    q["workloads"] = [dict(deepcopy(w), id="work-" + str(i)) for i in range(size)]
    pin = digest(r)
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        result = plan(r, q, pin, "2026-09-20T12:00:01Z")
        samples.append(time.perf_counter() - start)
        assert result["status"] == "compatible" and len(result["allocations"]) == size
        assert result["budget_remaining"]["site-feed"]["power_watts"] == 0
    results.append(
        dict(
            pools=size,
            workloads=size,
            comparisons=size * size,
            median_seconds=statistics.median(samples),
            max_seconds=max(samples),
        )
    )
print(
    json.dumps(
        dict(
            schema="dimaggi-infrastructure-benchmark/v1",
            evidence_class="synthetic",
            repetitions=5,
            results=results,
        ),
        indent=2,
    )
)
