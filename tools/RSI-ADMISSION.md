# Records admission

`rsi_records.assess(record, now, artifacts=...)` admits the supplied artifact bytes
and checks each task's repository, snapshot, profile, objective, denominator,
unit and workload before awarding useful credit. Evidence references alone
produce `insufficient_evidence` with zero useful credit. The command-line records
reader has no artifact input and cannot award credit.

The evaluator opens one designated durable replay store internally. The owner
provisions it once with `provision_replay_store(db_path, anchor_path)` and configures
`DIMAGGI_RSI_REPLAY_ANCHOR` in the evaluator environment. The anchor retains the
database path, random store identity, current chain head and row count outside
the database. Keep that configuration and anchor inaccessible to candidates.
Caller-supplied `ReplayStore` objects, including subclasses, are refused on both
crediting APIs. In-memory and unpinned stores cannot award credit.

Consumption records artifact, payload, row and source identities in one transaction.
A separate lock serializes consumers through the durable anchor update. Reuse,
including byte-reserialized artifacts, is refused after restart. Database rollback,
a fresh replacement store and an old anchor disagree with the retained state and
refuse. A crash between database commit and anchor replacement also refuses until
owner recovery; the implementation does not silently repair that mismatch.

A caller controlling both the evaluator environment and anchor can replace the
whole registry. Separate-identity deployment and protection of that anchor remain
operator obligations, not guarantees of an in-process Python API. Trust bytes
remain externally authenticated and frozen by the owner. Hashes establish
consistency, not provenance or physical measurement truth. Only the synthetic
resource-count profile is admitted; no result authorizes execution, continuation
or genuine-data acceptance. `_assess_records` remains an internal consistency
calculator for chronology tests; consumers must use `assess`.
