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

`read_current(path, expiry_ledger=..., tenant=..., cluster=..., collection=..., namespace=..., now=...)`
samples lease age in short transactions before and after ledger access. The later wall-clock sample
controls projection freshness; caller `now` must be within two seconds of it.
The caller does not control freshness.
The reader opens the collector database read-only and never writes to it. Provision
an expiry ledger once with `initialize_expiry_ledger(path)` as the evaluator
identity, then supply that path explicitly on every read. There is no default
location. The CLI requires `--expiry-ledger`; local fault configuration requires
`ExpiryLedger`. Missing, unwritable or corrupt ledgers refuse reads. The ledger
must be a regular file owned by the reader UID with mode 0600, one link and no
symlink. Opening uses `O_NOFOLLOW`; validation and I/O use that same descriptor.

Concurrent reads take no collector write reservation; ledger access uses a
bounded exclusive file lock. The reader ends its database transaction before
waiting on the ledger lock or syncing the ledger. A second short read-only
transaction compares the lease owner, heartbeat, live flag, generation, projection
body and digest, and store identity with the first read. Any change refuses with
`watch changed during reader verification`; retry from a fresh read. Such a
changed snapshot never records expiry.

Collector transactions and reader samples
also use a cooperative lock on an empty `<database>.lease-lock` sidecar, with a five-second
wait limit (`collector lease sampling busy`). The lock excludes a renewal checked before
sampling but not yet committed. New SQLite databases use DELETE journal mode;
its SHARED read lock blocks commits. WAL readers can retain older snapshots, so
the cooperative lock is acquired before BEGIN in both journal modes. It is released
before any ledger lock or fsync. Deploy the collector and reader changes together
and restart old collectors; older collectors do not take this lock. The collector
creates the sidecar with mode 0644, so the reader needs only read permission.
Protect its parent directory and never remove or replace the sidecar while the
store is in use. A missing sidecar refuses reads. A separate inode avoids closing
a database descriptor that could release another connection's SQLite locks.
Private `:memory:` stores have no cross-process reader and need no sidecar.

Scope is checked before any ledger access. The ledger retains dead generations
by store identity, scope and generation. A reader never revives a recorded dead
generation, even if the clock rolls back or the collector rewrites that lease.
Reader observations do not close the collector's lease. The collector independently
refuses expired generations; a new acquisition still requires a new relist.

`close()` closes the collector lease. A reader records a coherently observed
closed lease, or a heartbeat at least 47 seconds old, as dead. Ages from 45 seconds
through less than 47 seconds refuse without recording expiry: a collector up to
two seconds behind the reader may still renew while its own age is less than
45 seconds. At reader age 47, no pending renewal remains under the cooperative
lock and the collector's own age is at least 45. Under the clock contract, that
expiry decision remains valid after the transaction ends and before the ledger
append. The read cutoff remains 45 seconds.

An already tombstoned generation always reports `collector generation previously
expired`. Otherwise a **live** lease with a heartbeat more than two seconds in the
future refuses with `collector heartbeat is in the future`, without writing to
the ledger. A closed lease still records death even with a future heartbeat.

Correct the clock disagreement and retry; the same live generation can be read once
its heartbeat and projection are current. The collector never sees reader
observations. A recorded expiry cannot be undone by a later backward clock step.
Restart the collector to acquire a new generation and relist; retain the ledger.

The collector commits its own lease closure when its checks detect expiry or
excessive future skew. Collectors also refuse renewal after 45 seconds of
monotonic elapsed time since their last checked renewal. Heartbeat persists the
wall time sampled in its owner check and re-checks monotonic elapsed time before
commit. A pause after that check can delay commit, but cannot replace the checked
timestamp with a later, fresh one; a reader waits for that transaction before
sampling. Recovery requires a new WatchStore acquisition and a new relist, not a
heartbeat of the old lease.

With an advancing reader wall clock, reads refuse at a heartbeat age of
45 seconds. Permanent expiry is recorded at age 47. These are lease-age bounds,
not guarantees about when a reader runs.
An unobserved forward clock jump followed
by rollback cannot be remembered. A rollback before any expiry observation can
extend a dead lease's apparent wall-clock lifetime if it lands after the last
heartbeat; repeated corrections have no finite real-time detection bound for
readers. A surviving collector checks elapsed time again before committing a renewal;
a restarted collector always needs a new generation and relist.

Protect the collector database and its parent directory so the evaluator identity
can read but cannot modify or replace them. The collector identity can forge
unkeyed observations, store identities and generations. The evaluator identity
can alter its own expiry ledger and process-local receipts; it cannot forge the
collector database when OS permissions separate those identities. These checks
are not a sandbox against arbitrary code in either trusted process. Protect the
ledger directory too, retain the ledger over restarts, and never reset it to clear
refusals. Restoring or deleting its history loses the rollback guarantee. A new
ledger deployment requires retiring old collector generations and relisting.
Clock integrity and physical truth remain trusted inputs.

Heartbeat age accepts the interval from -2 seconds through less than 45 seconds.
The same two-second tolerance applies to caller `now`. Collector and reader
clocks must stay within 2 s for the lease contract to hold.

If the collector clock
leads the reader by S seconds and then the collector dies, reads may return
CURRENT until S+45 s after the last heartbeat (measured on the advancing reader
clock). At exactly S+45 s they refuse; projection validity may refuse earlier.
Persistent "heartbeat is in the future" refusals mean the clocks must be fixed
before the reads are trusted. A single two-second backward step can add two
seconds to the apparent lifetime; it is not a bound on arbitrary collector lead.
Future heartbeats of a live lease do not record death. Projection freshness stays
strict: a future observation can temporarily refuse a read without killing the
lease. Repeated unobserved clock corrections have no finite real-elapsed bound.

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

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

Provisioning must complete successfully before the ledger is used. If file or
parent-directory fsync fails, initialization raises; a complete leftover header
may still parse on a later read, but it does not prove durable provisioning.
Do not automatically adopt that residue. Investigate the failure and provision
successfully before use, retiring any old generations before replacing a ledger.
Local source tests do not establish installed, crash, power-loss or physical
storage guarantees.
