# Topology replay, outcome metrics and journal portability

This candidate provides topology, outcome accounting and portable journals.
`topology.TopologyState` consumes attributed full snapshots through Topograph graph
and Kubernetes ResourceSlice adapters. It preserves provider labels and raw DRA
attributes, pool generations, opaque resource versions and object UIDs. It does
not discover fabric health, authorize claims, sum partitions or connect to an API.

The replay contracts were checked against the [Topograph graph documentation](https://docs.nvidia.com/topograph/engines/graph/)
labelled 1.0.0 and Kubernetes [resource/v1 types at v0.34.0](https://github.com/kubernetes/api/blob/v0.34.0/resource/v1/types.go)
on 24 September 2026. These references qualify fixture structure only. Installed
provider/driver configurations and optional features need separate conformance.
Unknown versions refuse. Incomplete DRA pools remain explicit. Topograph instance
IDs do not prove incarnation identity. A DRA slice name does not prove node UID.

Each projection is scoped to one tenant and cluster. Source sequence is local
adapter order, never an integer interpretation of Kubernetes resourceVersion.
Conflicting versions and gaps taint a source until a new-epoch full relist. Full
snapshots replace prior records, preserving deletion and UID-change effects in
snapshot identity. The separate bounded TLS list/watch client and durable projection
are documented in [collection support](TOPOLOGY-COLLECTION.md). Independent sources
remain separate; overlapping provider IDs with differing descriptions are flagged
as conflicts. Automatic identity joining and conflict arbitration are not qualified. Callers must not choose a convenient source to hide a conflict.

`outcome_metrics.outcome_metrics` requires an aligned window, fixed comparison
context, explicit outcome records, meter scope, attribution and complete cost-rate
intervals. It retains failed/discarded work and whole-window allocated idle cost.
Measured and modelled energy stay distinct. Missing energy, counter resets and
sample gaps yield unknown energy. Counter deltas cannot establish peak-power budget
compliance. Power intervals use a declared piecewise-constant model, whose fidelity
requires meter qualification. No utilization/TDP conversion or PUE uplift is inferred.
No overlapping meter sum is accepted by this single-meter contract. Allocation
uncertainty remains explicit; it is not a statistical confidence interval.

Journal files use descriptor-owned byte-range locks on Darwin and qualified LP64
Linux ABIs. Tests check competing processes, hard-link aliases, unrelated descriptor
closure, writer death and committed-state recovery. The managed Linux x86_64 interpreter exercises the fallback ABI. Native Linux
arm64 and Darwin qualification remain separate, unverified platform obligations. No power-cut/filesystem or live
cluster acceptance follows from process-kill tests.

Engineering checks do not replace independent acceptance or authorize deployment.

`reconcile_allocations(raw_inputs)` checks one meter and exact `[start_s,end_s)`
window, including every tenant and retry. The caller must serialize persistent
inserts and submit the complete group. Window labels do not separate claims on the
same physical interval. Repeated attempt IDs are refused for that meter and exact
interval regardless of tenant or window labels. Non-exact intervals, including
overlapping aliases, refuse. Distinct retry attempt IDs remain subject to the same
fraction total. Meter samples, scope and whole-meter cost intervals must
agree. Fractions use exact Decimal addition with zero tolerance: a sum above one
is refused. Each allocation receives the same fraction of energy and cost.
The result contains `allocations`, `allocated_fraction`, `unattributed_fraction`,
`unattributed_energy_j` and `unattributed_cost_usd`. Missing measurements remain
null. A single `outcome_metrics` call cannot establish complete group coverage.
Cost intervals describe the whole meter; cost without an energy allocation stays
unknown with `cost_allocation_missing`. Decimal measurement inputs are limited to 64 decimal
digits and adjusted exponents from -100 through 100. Calculations use a private
512-digit context; ratios may round at that precision.

A power budget verdict of `within` requires measured intervals no longer than one
second. It describes the declared piecewise-constant interval model, not an
independent instantaneous-peak measurement. Longer intervals and modelled energy
leave the verdict unknown; a measured average above the budget still proves an
exceedance. `power_budget_basis` states this resolution rule. Counter-only energy
cannot establish peak power. Unknown verdicts retain `power_peak_unqualified`.

Map preempted attempts to `failed`; retries retain distinct attempt IDs and all
failed and idle energy/cost. Consumers must retain the returned `issues` and exact
decimal strings. The receiver does not implement the application's ledger review
or SQLite storage. Training quality targets and observations may be signed finite
values; resource, time, energy and cost inputs remain nonnegative.
