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
The reader opens the published snapshot read-only and never writes to it. Provision
an expiry ledger once with `initialize_expiry_ledger(path)` as the evaluator
identity, then supply that path explicitly on every read. There is no default
location. The CLI requires `--expiry-ledger`; local fault configuration requires
`ExpiryLedger`. Missing, unwritable or corrupt ledgers refuse reads. The ledger
must be a regular file owned by the reader UID with mode 0600, one link and no
symlink. Opening uses `O_NOFOLLOW`; validation and I/O use that same descriptor.

Concurrent reads take no collector write reservation; ledger access uses a
bounded exclusive file lock. The reader ends its database transaction before
waiting on the ledger lock or syncing the ledger. A second short read-only
transaction reopens the published path. A newer heartbeat is accepted only when
projection bytes and digest, store identity, lease owner and generation are
unchanged and the heartbeat has not regressed. Every lease and ledger decision is
re-derived from the second snapshot. Projection or generation changes refuse with
`watch changed during reader verification`. Retry with a fresh read and current
caller time. Frequent projection changes or replacement during each SQL read can
still starve every attempt; there is no reader availability SLA.

The collector keeps its writable SQLite database in `<database>.collector/writer.db`.
That directory must be owned by the collector and mode 0700. The writer uses WAL
and a collector-only lock inside that directory; readers cannot open either the
lock or the private WAL/SHM files. The reader-facing `<database>` is a complete
SQLite snapshot in DELETE journal format, published by atomic replacement after
each committed change. Access comes from `WatchStore` constructor configuration:
`publication_mode=0o400` and `publication_gid=None` default to owner-only read
access and the collector’s effective group. To grant another reader group access,
pass `publication_mode=0o440, publication_gid=gid` on every restart. Write bits
are always stripped. The collector does not learn access from the published file
or consult an existing `publication_access` table; legacy tables are ignored.
Revoke a configured grant by changing configuration and restarting the collector.
A chmod is only a change to the current inode and does not update configuration.
Keep the parent directory writable only by the collector. Reader and
collector identities must be distinct for this permission boundary to hold.

On Linux, readers open with `O_RDONLY|O_NOFOLLOW`, hold that descriptor, and use
`mode=ro&immutable=1` through `/proc/self/fd`. They fstat the held inode and confirm
its device/inode still matches the pathname after SQLite finishes, including
SQLite implementations that canonicalize the procfs link. Non-Linux readers fail
closed. No tool may open the published file read-write; use immutable read-only
access for inspection and the private writer for collector changes. They need neither write
permission nor public WAL/SHM files. Each transaction copies raw rows, ends, and
only then parses JSON or computes digests. `quick_check` reads the immutable
snapshot and cannot lock the private writer database. The comparison opens the
pathname again so it sees the latest published inode. A paused reader, overlapping
readers, or a hostile process holding shared flock or POSIX locks on a published
file cannot delay the collector through those locks. Readers can still exhaust
storage by retaining descriptors to replaced inodes. Each pins a whole snapshot:
the 10,000-item fixture with 600-byte padding occupies 7,438,336 bytes per initial
publication, and later SQLite free-page growth can increase it. At three such
publications per 30-second cycle, pinned space grows by 22,315,008 bytes per cycle
(743,833.6 bytes/s). Inspect open deleted files with `lsof +L1` and filter its NAME
column for the published directory; monitor free space too. Readers must close
snapshots promptly. CPU, storage and scheduler saturation remain availability
limits. Publication copies the entire database and fsyncs the file and directory
on every commit. Timings measured on tmpfs do not qualify durable disk latency.

This separation is necessary for hostile readers: in shared WAL mode, read access
to `-shm` suffices to hold a POSIX read lock on SQLite's writer-lock byte and block
heartbeats. WAL alone removes normal SQL-reader contention but not that attack.
Startup first probes the old public `<database>.lease-lock` with a nonblocking
exclusive lock and refuses if held. Otherwise it removes that file, so legacy readers that
require it will refuse. A leftover lock is unsafe for mixed-version operation. No reader-accessible coordination file has a forced 0644 mode.

Stop old collectors and upgrade readers and collectors together. Read-only
publication prevents an ordinary legacy collector from opening it for writes.
This does not fence root, an owner that restores write permissions, or a legacy
writer with an existing writable descriptor. The legacy lock probe detects a
held transaction lock, not an idle process. The protocol marker is not proof of
rename publication: an old writer can preserve it. Comparing mtime and ctime
cannot distinguish in-place writes reliably, so readers do not use that test. On first open,
the collector imports an existing database into its private directory, starts a
new generation, and requires a relist. After backup it removes public `-wal`,
`-shm` and `-journal` residue; publication also removes those names before rename.
Temporary copies and both startup probe files are created inside the
collector-owned 0700 `<database>.collector/` directory. Under the writer lock,
startup cleans matching, collector-owned regular copies only there. Modes from
0000 through 0777 are accepted, with write bits stripped. Cleanup temporarily
restores owner read to inspect a copy's lock, so crash residue with mode 0000
can also be removed. Locked copies retain their modes; symlinks, hardlinks and other
nonregular entries are preserved. Cleanup and publication hold the same exclusive writer lock and cannot overlap. Per-entry cleanup exceptions warn and leave startup running; process cancellation still propagates. Linux cleanup uses a verified no-follow descriptor to change only the crash copy's mode, even when no-follow path chmod is unsupported. If cleanup or mode restoration fails, the warning requires operator attention; deletion and mode restoration are not guaranteed after an I/O failure.

Before its first write, startup checks write and search access to the public directory using effective credentials, and checks sticky-directory replacement restrictions. The kernel access check refuses unavailable access, including directory ACL restrictions and read-only mounts, without creating a public probe file. Startup repeats the check under the writer lock before lease acquisition. Permissions and mounts can change after the check; publication errors still use the failure handling below.
 Modes
without owner read can prevent the collector identity from reading the public
snapshot; they do not block private crash-copy cleanup.
Startup never scans the public directory for `.watch-*`
cleanup, including scoped names: a name cannot prove temp provenance. For a one-time migration,
stop collectors, identify leftover copies from the old installation, and remove
only confirmed temporary files. The private directory and publication must share
a filesystem and mount. A separate bind mount is unsupported even with the same
device number. Linux startup compares mount IDs through `/proc/self/fdinfo`;
missing or unreadable mount identity metadata refuses startup with
`procfs mount identity access required` before lease acquisition. EXDEV during publication raises the same mount-requirement error.
Publication syncs both the public and private directories. Live copies hold
a lock that cleanup never waits for. Copies remain 0600 through data fsync, then receive the
published mode/group immediately before rename. POSIX ACLs are not preserved.
New readers require the publication-protocol marker. Keep the private directory with its published snapshot during backup and
recovery; do not edit or delete either while a collector is active. Private
`:memory:` stores have no published cross-process reader.

Scope is checked before any ledger access. The ledger retains dead generations
by store identity, scope and generation. A reader never revives a recorded dead
generation, even if the clock rolls back or the collector rewrites that lease.
Reader observations do not close the collector's lease. The collector independently
refuses expired generations; a new acquisition still requires a new relist.

`close()` commits a closed lease and publishes it if the public path exists.
If this object already published closure, close releases resources without
republishing. No later operation by this object withdraws that closed snapshot.
Failure handling depends on the failed operation and lease ownership:

| Situation | Public outcome |
|---|---|
| Startup lacks effective write/search access or sticky-directory replacement permission | Refuse before acquisition; preserve the running lease and public inode. |
| Writer lock cannot be acquired | Mark lost, release the publication pin and refuse explicitly. Preserve the public inode without unsynchronized unlink; its existing serving cutoff applies. |
| Fallback retirement (`live=0`) commits and republishes successfully, including a late heartbeat or a COMMIT failure followed by successful fallback | Keep the closed snapshot so readers can record death. Later operations by this object preserve it; close does not republish. Do not withdraw. |
| This object owns the lease, has not published closure, and durable closure or publication cannot be established | Unlink before re-raising. |
| This object never owned the lease because acquisition failed, including at COMMIT | Never touch the prior owner’s snapshot. |
| The ownership query itself fails | Under the writer flock, compare the public inode with a retained descriptor for this object’s last publication. Unlink a match only before successful closure publication; preserve any other inode. |
| Watch input is rejected, including non-object metadata in ADDED or BOOKMARK | Validate shapes explicitly, then publish `resync_required`. The same process can relist. |
| `fail()` or invalidation encounters private integrity or I/O failure | Withdraw unless this object already published closure. |

The read-only ownership descriptor pins at most one inode per collector
object that has not yet been garbage-collected, preventing inode reuse during ownership checks. Replacement, withdrawal
and successful close release the pin. Garbage collection closes it without
publishing, retiring or unlinking. Fork inherits the pin: the child must release
its copy or exit before that inode can be reclaimed. The pin is not inherited
through exec. Readers never use this descriptor or the writer flock. Successful retirement is tracked separately from rejected
input; exception class does not decide whether to withdraw.
After successful unlink readers refuse as unavailable without recording death.
A failed close raises and remains retryable; a retry completes once its private
closure succeeds and closure is published or the public path is absent. If permissions
or filesystem failure prevent both actions, the old snapshot can remain readable
until its 45-second serving cutoff. A crash before rename leaves the previous
complete snapshot and possibly a temp copy; after rename it leaves the new complete
snapshot. A crash before failure cleanup has the same lease-age bound. Power-loss
durability before directory fsync is unqualified. Reads already in progress may
finish from an earlier snapshot; withdrawal does not revoke returned receipts.

Each file-backed collector retains one read-only writer-lock descriptor until successful close or
object reclamation. Before a write, it takes that lock and verifies the lock
pathname and mount placement. A hidden private directory or missing procfs then
raises an explicit placement error. Under the retained lock, failure cleanup
withdraws only the collector's pinned publication; a successor's inode and a
published retirement are preserved. If the lock itself cannot be acquired, the
collector marks its lease lost and releases its publication pin. It cannot
safely unlink without serialization, so the existing 45-second serving cutoff
applies. Once the lease is lost, close releases the local connection and retained
descriptors without checking placement or changing the publication. If close
itself encounters the placement failure, it reports that failure; a second close
releases local resources even if access has not been restored. A successful
retirement remains published.

Readers serve a live snapshot only if the held inode's kernel ctime minus its
checked heartbeat H is between `-CLOCK_TOLERANCE_SECONDS = -2` and
`COMMIT_BOUND_SECONDS = 10`, inclusive. Either violation refuses without a
tombstone. The lower bound detects H ahead of the rename timestamp after a
backward collector wall step. It does not solve arbitrary clock corrections.
The 10-second claim holds up to the filesystem's timestamp granularity.

At acquisition a probe is renamed from one private name to another until its ctime
advances beyond its pre-rename sample. The mount check confirms that the publication directory
is on the same mounted filesystem as the private directory. Same-directory and cross-directory
renames update inode ctime on supported filesystems; a regression test checks the
actual private-to-public rename. This startup witness is not a timestamp-error
bound or verification of every filesystem. The loop has a two-second deadline to
allow for filesystem timestamp granularity; if ctime does not advance, startup
refuses. Readers use ctime from the
pre-read fstat. This is a tighter witness than a later sample if chmod or unlink
changes metadata during SQL. The post-SQL pathname stat is still necessary for
device/inode identity; it needs no second fstat of the pinned descriptor.
A chmod before opening, or before the second read, can still cause a spurious
refusal until the next timely publication. No earlier rename timestamp can be
recovered from that changed ctime.

This requires Linux local filesystems with rename-updated ctime and the same host
wall-clock domain for collector, reader and filesystem. Cross-host readers and
network filesystems are unsupported. No tolerance is added to the upper bound.
The advancing-clock proof below does not cover arbitrary wall-clock corrections.

Lease acquisition, heartbeat and projection writes all store the checked wall
timestamp. The collector retains its post-publication wall and monotonic elapsed
checks as defence in depth, retiring a late operation and requiring restart and
relist. Those checks are not the visibility bound: the reader's ctime check is.
First acquisition and migrated snapshots pass through the same publication path.

The serving cutoff remains age 45. Ages 45 through less than 57 refuse without
recording death. A closed lease can record death. Otherwise, recording death
requires a timely publication and age at least 57 at the wall sample T0 taken
before opening its inode.
For the proof, call the old heartbeat H0 and a missing renewal's checked heartbeat
H1. A missing renewal is renamed after T0, so its kernel publication time P > T0
(up to filesystem timestamp resolution). If any reader can serve it, P-H1 <= 10,
so H1 >= P-10 > T0-10. A correct collector renews only while H1-H0 < 45.
Thus H0 > T0-55: a recorded death at T0-H0 ≥ 57 never coincides with a correct
renewal that could still be served.
The existing two-second margin is retained conservatively, including timestamp
resolution; it is not permission for cross-host use. A renewal outside the ctime
bound can never be served, even while its collector is stopped before retirement.
A slow reader uses T0 for death and a later sample for serving, so it may refuse
without recording death even beyond age 57.

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
commit. The reader ctime check refuses publication delayed beyond the bound; the
collector post-publication check retires it when execution resumes. Readers do not wait for that operation.
Recovery requires a new WatchStore acquisition and a new relist, not a heartbeat
of the old lease.

With an advancing reader wall clock, reads refuse at a heartbeat age of
45 seconds. Permanent expiry requires pre-open age 57 or a closed lease. These are lease-age bounds,
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
