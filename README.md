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
and require kernel ctime minus its checked heartbeat to be at most 10 seconds.
Late publication refuses without a tombstone, even if the collector is stopped.
There is no extra tolerance in that comparison: collector, reader and filesystem
must share one host wall clock. Cross-host/network filesystems are unsupported;
non-Linux readers fail closed. The 57-second death cutoff is conservative: a
missing servable renewal renamed after T0 must have checked H1 > T0-10, and a
correct renewal requires H1-H0 < 45, hence H0 > T0-55. The retained two seconds
provide margin, including timestamp resolution, with advancing wall clocks.
Collector post-publication retirement remains defence in depth, not a visibility
bound. Acquisition and projection writes also store checked heartbeats.

The collector uses a private WAL database inside a collector-owned 0700 directory
and publishes immutable DELETE-format snapshots on fresh inodes. Copies stay
0600 through data fsync, then receive the published mode/group before rename.
Readers take no collector locks. On any publication failure the collector tries
to unlink the public path; readers then refuse as unavailable without tombstones.
If publication and unlink both fail, the old snapshot may serve until age 45;
close remains retryable. A crash can leave the last complete snapshot readable
until its cutoff, plus a temp copy. Startup removes owned inactive temp copies,
the legacy public lease lock and stale journals after migration backup. No tool
may open the published file read-write. Upgrade readers and collectors together.

Heartbeat-only changes can pass the second read if projection, identity and
generation are unchanged and all newer lease/ledger checks pass. Frequent
projection changes or replacement during SQL reads can still starve readers;
there is no availability SLA. Readers must close snapshots promptly: open old
inodes pin whole copies and can exhaust disk space. The 10,000-item/600-byte-padding
fixture initially uses 7,434,240 bytes per publication; three retained copies per
30 seconds add 22,302,720 bytes per cycle. SQLite growth can increase this. Monitor
free space and open deleted files with `lsof +L1`. Every commit copies the entire
database; tmpfs timings are not durable-disk guarantees. Use distinct service
identities and protect the parent directory. See the topology collection profile
for the proof, failure semantics, migration and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
