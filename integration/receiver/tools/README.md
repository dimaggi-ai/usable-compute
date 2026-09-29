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
age 57 measured before opening the snapshot. Each lease acquisition, heartbeat
or projection write must commit and publish within 10 seconds of its validity
check; a late operation
abandons its generation and needs a restart and relist.

The collector uses a private WAL database inside a collector-owned 0700 directory
and atomically publishes a complete read-only snapshot. The snapshot starts at
0600; replacements retain its mode and group. Readers need no WAL/SHM files or
public lock sidecar. Shared locks on a published snapshot cannot block collector
writes. Use distinct reader/collector identities and protect the parent directory;
CPU and storage saturation remain environmental limits. Upgrade both components
and restart old collectors together. See the topology collection profile for the
storage layout, migration and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
