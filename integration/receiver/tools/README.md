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

A future heartbeat on a live lease writes no tombstone;
closed leases still do. Reads refuse at age 45 seconds, but ages from 45 through
less than 47 seconds do not record expiry because a collector two seconds behind
may still renew. Coherent age 47 or a closed lease permits a permanent tombstone.
Collector and reader upgrades must be deployed together and old collectors
restarted to use the cooperative sampling lock. See the topology collection
profile for the transaction and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
