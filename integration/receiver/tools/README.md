# Acceptance gate

`check_acceptance.py` compares JUnit test identities and suite counters with the
committed receiver or tools manifest. It checks the configured policy marker and
the source-pin-specific expected failure. JUnit XML is unauthenticated: a forged
report can also forge the marker.

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

Run `python integration/receiver/tools/ci_test_manifest.py --check` from the
repository root with the source bundle configured to check collection against
the manifests. Omit `--check` only for an intended collection change, then review
the manifest diff. Tests added for a fix must remain in the workflow selection.

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
New snapshots start at 0400. The private database retains the last observed
mode/group for restart after withdrawal; write bits are stripped. ACLs are not retained.
Readers take no collector locks. Publication failure or a private write failure
while the object owns the lease attempts withdrawal under the writer flock;
readers then refuse as unavailable without tombstones.
If publication and unlink both fail, the old snapshot may serve until age 45;
close remains retryable. A crash can leave the last complete snapshot readable
until its cutoff, plus a temp copy. Startup removes only owned, regular, inactive
temps matching this store’s scoped prefix or the exact legacy pattern
`.watch-[a-z0-9_]{8}`. Other files are preserved. Startup refuses a held legacy
lease lock; otherwise it removes that lock and stale journals after migration
backup. Read-only modes block ordinary legacy writes, but root or an owner
changing permissions can bypass them. Stop old collectors and upgrade together.

Heartbeat-only changes can pass the second read if projection, identity and
generation are unchanged and all newer lease/ledger checks pass. Frequent
projection changes or replacement during SQL reads can still starve readers;
there is no availability SLA. Readers must close snapshots promptly: open old
inodes pin whole copies and can exhaust disk space. The 10,000-item/600-byte-padding
fixture initially uses 7,438,336 bytes per publication; three retained copies per
30 seconds add 22,315,008 bytes per cycle. SQLite growth can increase this. Monitor
free space and open deleted files with `lsof +L1`. Every commit copies the entire
database; tmpfs timings are not durable-disk guarantees. Use distinct service
identities and protect the parent directory. See the topology collection profile
for the proof, failure semantics, migration and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
