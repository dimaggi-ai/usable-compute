# Topology replay, outcome metrics and journal portability

This candidate adds two read-only Python modules and a Linux locking path.
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
snapshot identity. No incremental watch client is implemented. Independent sources
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
closure, writer death and committed-state recovery. Actual Linux arm64 container
checks are distinct from native Darwin arm64 checks. Linux x86_64 uses the documented
ABI but has not been exercised by this candidate. No power-cut/filesystem or live
cluster acceptance follows from process-kill tests.

Engineering checks do not replace independent acceptance or authorize deployment.
