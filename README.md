# DIMAGGI AI — Usable Compute

**Turn infrastructure into dependable, usable compute.**

DIMAGGI connects workload planning, compute, networks, storage, power and governed
operations through explicit contracts and traceable evidence. The aim is to make
infrastructure decisions account for the whole system: what a workload needs,
where it fits, which changes are authorized, and what actually happened.

The installable [infrastructure receiver](integration/receiver/INFRASTRUCTURE.md)
combines versioned capabilities, exact software compatibility, shared resource
budgets and observed-state reconciliation. Its CPU evidence connects to TENWA's
signed execution configuration. [Provider telemetry and source review](integration/receiver/OPERATIONS.md)
now join the same assessment, with persistent drift findings and replayable failure
campaigns. Training, inference and agentic workloads share
the planning contract; live TPU/GPU execution remains a separate validation step.
See the [delivery assessment](integration/receiver/DELIVERY-ASSESSMENT.md) for the
boundary between implemented software, local lab evidence and remaining proof.

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

Turning AI infrastructure investment into usable compute: a portfolio covering
scheduling, networking, reliability, power, cooling, placement, and governed operations.

See the [Infrastructure Intelligence portfolio assessment](INFRASTRUCTURE_INTELLIGENCE.md)
for implementation ownership, capability mapping and explicit integration boundaries.

For collective reviews, use the [portfolio workflow](PORTFOLIO.md) to discover
membership from GitHub custom properties and capture the exact commits to analyze.

This repository is the entry point to DIMAGGI AI's open, reproducible infrastructure
portfolio. Served via GitHub Pages; it indexes the series and installable standards.
Source: `index.html`.

Live: https://dimaggi-ai.github.io/usable-compute/

[Cover artwork](COVER_ARTWORK.md): visual index, download links and sharing-image setup
for the public portfolio.

[Cover strategy](COVER_STRATEGY.md): clear research copy, restrained illustrations,
and proofreading checks.

Topology leases require collector and reader clocks within 2 s. A collector clock
lead of S seconds can leave a dead generation readable until S+45 s after its last
heartbeat on an advancing reader clock. Reads refuse at exactly S+45 s. Persistent
"heartbeat is in the future" refusals mean the clocks must be fixed before the
reads are trusted.

A future heartbeat on a live lease writes no tombstone; closed leases still do.
Reads refuse at age 45 seconds. The band from 45 through less than 57 seconds
refuses without recording death. Permanent expiry requires a closed lease or
age 57 measured before opening the snapshot. Linux readers pin the exact inode
and require -2 ≤ kernel ctime minus its checked heartbeat ≤ 10 seconds.
Either bound refuses without a tombstone, even if the collector is stopped.
The 10-second claim holds up to filesystem timestamp granularity. The code
compares the recorded timestamps directly, with no added upper-bound tolerance. Collector, reader and filesystem
must share one host wall clock. Cross-host/network filesystems are unsupported;
non-Linux readers fail closed. The 57-second death cutoff is conservative: a
missing servable renewal renamed after T0 must have checked H1 > T0-10, and a
correct renewal requires H1-H0 < 45, hence H0 > T0-55. The two-second margin
allows for timestamp resolution and assumes advancing wall clocks.
Collector post-publication retirement remains defence in depth, not a visibility
bound. Acquisition and projection writes also store checked heartbeats. Startup
checks that rename advances ctime, allowing up to two seconds for granularity.
Readers use the pre-read descriptor timestamp; metadata changes before opening
can still cause refusal until the next timely publication.

The collector uses a private WAL database inside a collector-owned 0700 directory
and publishes immutable DELETE-format snapshots on fresh inodes. Copies stay
0600 through data fsync, then receive the read-only mode/group before rename.
Access is explicit: `WatchStore(..., publication_mode=0o440, publication_gid=gid)`
grants group read access; the default is 0400 with the collector’s effective group.
Supply the configuration on every restart. An explicit group must be the
collector’s effective group or one of its supplementary groups; construction
refuses other groups before creating files. Write bits are always stripped;
on-disk permissions and any legacy `publication_access` table are ignored.
Modes from 0000 through 0777 are accepted, with write bits stripped. Modes
without owner read are supported; they can prevent the collector identity from
reading the public snapshot, but do not prevent private crash-copy cleanup.
To revoke a configured grant, change the configuration and restart the collector;
chmod alone does not change configuration. ACLs are not retained.
Readers take no collector locks. Failure handling follows this table:

| Situation | Public outcome |
|---|---|
| Fallback retirement commits and republishes successfully, including a late heartbeat | Keep the closed snapshot so readers can record death. Later operations by this object preserve it; close does not republish. |
| This object owns the lease, has not published closure, and durable closure or publication fails | Unlink before re-raising. |
| Acquisition fails before ownership, including at COMMIT | Preserve the prior owner’s snapshot. |
| The ownership query fails | Under the writer flock, before successful closure publication, unlink only if the public inode matches this object’s pinned last publication. Preserve another generation’s inode. |
| Watch input is rejected, including malformed metadata | Publish `resync_required`; allow relist in the same process. |
| Invalidation itself encounters private integrity or I/O failure | Withdraw unless this object already published closure. |

After unlink, readers refuse as unavailable without adding tombstones.
If unlink fails too, the error propagates and the old snapshot may serve until
age 45 seconds; close remains retryable. A crash can leave the last complete snapshot
readable until its cutoff, plus a temp copy. Temporary copies and both startup
probe files are created inside the collector-owned 0700 `<database>.collector/`
directory. Under the writer lock, startup cleans matching, collector-owned
regular copies only there, temporarily restoring owner read when needed to check
the copy lock. Locked copies retain their modes; symlinks, hardlinks and other
nonregular entries are preserved. Per-entry cleanup exceptions warn and leave startup running; process cancellation still propagates. Linux cleanup uses a verified no-follow descriptor to change only the crash copy's mode, even when no-follow path chmod is unsupported. If cleanup or mode restoration fails, the warning requires operator attention; deletion and mode restoration are not guaranteed after an I/O failure.

Before its first write, startup checks write and search access to the public directory using effective credentials, and checks sticky-directory replacement restrictions. The kernel access check refuses unavailable access, including directory ACL restrictions and read-only mounts, without creating a public probe file. Startup repeats the check under the writer lock before lease acquisition. Permissions and mounts can change after the check; publication errors still use the failure handling below.

Cleanup and publication hold the same exclusive writer lock and cannot overlap. Startup never
scans the public directory for `.watch-*` cleanup. Public files with that prefix
are preserved, including scoped names: a name cannot prove temp provenance. For a one-time migration,
stop collectors, identify leftover copies from the old installation, and remove
only files confirmed to be temporary. Do not remove other stores’ publications
or operator files. The private directory and publication must share a filesystem
and mount; a separate bind mount is unsupported even with the same device number.
Linux startup checks mount IDs through `/proc/self/fdinfo`; missing or unreadable mount identity metadata refuses startup with
`procfs mount identity access required` before lease acquisition. EXDEV during publication raises the same mount-requirement error. Both directories are synced
after rename. The private rename probe checks ctime advancement on this filesystem;
it does not verify timestamp-error tolerance or power-loss durability. Startup refuses a held legacy
lease lock; otherwise it removes that lock and stale journals after migration
backup. Read-only modes block ordinary legacy writes, but root or an owner
changing permissions can bypass them. Stop old collectors and upgrade together.

The read-only ownership descriptor pins at most one inode per collector
object that has not yet been garbage-collected, preventing inode reuse during ownership checks. Replacement, withdrawal
and successful close release the pin. Garbage collection closes it without
publishing, retiring or unlinking. Fork inherits the pin: the child must release
its copy or exit before that inode can be reclaimed. The pin is not inherited
through exec. Readers never use this descriptor or the writer flock.

Heartbeat-only changes can pass the second read if projection, identity and
generation are unchanged and all newer lease/ledger checks pass. Frequent
projection changes or replacement during SQL reads can still starve readers;
there is no availability SLA. Readers must close snapshots promptly: open old
inodes pin whole copies and can exhaust disk space. The 10,000-item/600-byte-padding
fixture used 7,438,336 bytes per initial publication in an earlier local run;
three such retained copies per 30 seconds add 22,315,008 bytes per cycle.
Size varies with database layout and SQLite growth. Monitor
free space and open deleted files with `lsof +L1`. Every commit copies the entire
database; tmpfs timings are not durable-disk guarantees. Use distinct service
identities and protect the parent directory. See the topology collection profile
for the proof, failure semantics, migration and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
