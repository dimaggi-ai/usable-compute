# Read-only topology collection profile

`topology_watch.WatchStore` accepts core/v1 Nodes and resource.k8s.io/v1 ResourceSlices and namespaced ResourceClaims. The DRA normalization profile remains Kubernetes 1.34.0. API type acceptance is not a claim of conformance to every server/driver version. Qualification uses a local TLS peer plus recorded fixtures; no customer cluster was contacted.

`topology_collect.collect` uses an explicitly supplied TLS origin, CA and bearer credential. It issues GET only, follows no redirects and uses no ambient kubeconfig/proxy. The subprocess deadline includes DNS. Responses are capped at 8 MiB; list collections at 10,000 objects; watch batches at 1,024 frames. Paginated lists refuse until a deployment uses a supported bounded collection. A deployment must provision read/list/watch permissions only for its collections and scope. Namespace names alone are not immutable tenant identity; provision the collector and scope under a trusted operator.

A full list establishes an opaque resource version. Ordered watch batches commit atomically. Stream failure, expired resource versions, queue overflow, UID mismatch and competing collectors invalidate the projection. Restart requires relist. Resource versions are never numerically ordered. The execution reader requires a numeric string to match the binding contract; other opaque versions remain inspectable but cannot produce an execution binding. Ordering is supplied by the single TLS stream. New collection attempts with changed TLS origin/CA require relist. No health, capacity or execution permission is inferred from a connection.

SQLite FULL synchronous transactions retain the last committed projection. This is a local persistent cache, not an HA service. Trusted owner-controlled storage is required; an attacker able to replace its database can falsify observations. The in-memory source session prevents a stale collector from advancing a newer session. A conflicting collector can force resynchronization; run one owner per scoped collection.

The trusted collection host timestamps reads with its UTC clock and refuses responses that arrive after the configured validity interval. Observations cannot authorize execution. A candidate invalidates when a relevant snapshot changes or expires; uncertain submitted work requires reconciliation without retry. Topology snapshots cannot replace the executor's independent grant, target, namespace, object and attempt checks.

Reference semantics: https://kubernetes.io/docs/reference/using-api/api-concepts/ (opaque resource versions; watch loss and 410 relist). Exact provider/server qualification remains in the release support matrix.

Collectors trust ordering from the authenticated list/watch stream; Kubernetes
resource versions are opaque and are not sorted numerically. Empty watch responses
do not refresh observation time or expiry. A valid typed BOOKMARK may refresh the
stream observation. Failed relists mark persisted state as requiring resync.

Call `validate_inventory_agreement(inventories)` before composing several complete
WatchStore inventories: contradictory records in the same scope refuse with
`source_conflict`. TopologyState also compares DRA slices by driver, pool and name.
The receiver cannot enforce this helper in the application's composition path;
that consumer must call it or enforce equivalent checks.

The 1,024-event batch bound remains a refusal boundary. Sustained event rates above
it can prevent progress and require a shorter collection window. Node heartbeat
resource-version changes still invalidate dependent snapshots. This conservative
behavior needs live-cluster liveness qualification before a lab run; synthetic
TLS tests do not establish production event-rate capacity.

Lease acquisition, scope validation and invalidation of the old projection commit
in one transaction. Every acquisition assigns a new generation. Only a successful
relist for that generation can make its projection current. Every projection
write checks the lease owner and generation inside its transaction, so a displaced
collector cannot overwrite or invalidate its successor's projection.

`read_current(path, tenant=..., cluster=..., collection=..., namespace=..., now=...)`
uses one wall-clock sample for lease liveness and projection freshness. Caller
`now` must be within two seconds of that sample; it does not control freshness.
The reader requires write access to the existing database to record lease expiry.
It never creates a missing store. Read-only storage fails closed.

`close()` closes the lease. A reader or collector that observes a future heartbeat
or a heartbeat at least 45 seconds old durably closes that generation. A later
wall-clock rollback cannot reopen it. Collectors also refuse renewal after
45 seconds of monotonic elapsed time since their last renewal. Recovery requires
a new WatchStore acquisition and a new relist, not a heartbeat of the old lease.

With an advancing wall clock, a crash is detected on the first read at least
45 seconds after the last heartbeat. That is a lease-age bound, not a guarantee
that a reader runs within 45 seconds. An unobserved forward clock jump followed
by rollback cannot be remembered. A rollback before any expiry observation can
extend a dead lease's apparent wall-clock lifetime if it lands after the last
heartbeat; repeated corrections have no finite real-time detection bound for
readers. A surviving collector's monotonic check prevents renewal after suspension,
and a restarted collector always needs a new generation and relist. These checks
assume the owner protects the database and clock; they do not detect a forged DB.

`collect` renews before each bounded TLS read. Quiet streams trigger a relist when
the remaining inventory lifetime is at most twice the configured TLS timeout
plus two seconds, reserving time for both a watch response and its next relist.
Callers must leave enough scheduling margin to finish that relist before expiry. HTTP 410 and watch ERROR 410 trigger a relist; a failed relist or other
stream error invalidates the projection and exits. Empty batches renew only the
lease. Manual loops must run often enough to renew before lease expiry; a
single gap of 45 seconds between renewals refuses. The maximum 31-second TLS
operation leaves less than 14 seconds for processing and scheduling before
renewal. Callers must schedule renewals before the lease expires.
Fake-transport tests cover quiet streams and 410 recovery, not live-cluster
liveness or scheduler capacity. A one-shot collector cannot supply a current
binding after it closes.

The reader returns a dict-compatible, process-local verified receipt. Binding
production accepts only an unmodified receipt and re-reads its store, checking
tenant and scope and refusing changed state. Serialization loses verification.
The store path, evaluator process and collector credentials must be owner-controlled;
this receipt does not authenticate data from a malicious store owner or constrain
the executor's placement. Drift after the final read remains a consumer boundary.
