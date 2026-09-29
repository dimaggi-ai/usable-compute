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
A separate lock refuses concurrent consumers with `replay_busy` until the durable
anchor update finishes. Reuse,
including byte-reserialized artifacts, is refused after restart. Database rollback,
a fresh replacement store and an old anchor disagree with the retained state and
refuse. A crash between database commit and anchor replacement also refuses until
owner recovery; the implementation does not silently repair that mismatch.

Inspect the interrupted transaction with:

```sh
python tools/rsi_admission.py --recover-anchor /protected/replay-anchor.json
```

This prints every journal entry ahead of the anchor and exits with
`owner_confirmation_required`. Consumption entries identify the exact artifact,
payload, row and source hashes retained in the store. Check those entries against
the consumption request before repeating the command with
`--confirm-owner-recovery`. Recovery verifies the full journal and consumption
table, requires the retained anchor to match a journal prefix, appends a recovery
record, then advances the anchor atomically. All consumption stays consumed.
A second interrupted anchor write can be recovered the same way, including the
previous recovery record. A corrupt chain, foreign identity or nonmatching prefix
refuses even with confirmation. The flag records owner intent; OS permissions
must restrict who can invoke it.

Stores created before the journal schema require this same owner-confirmed
procedure before consumption resumes. The command reconstructs their original
consumption hash chain and verifies the retained anchor prefix. Confirmation
materializes that chain without changing any existing hash, then appends the
recovery record. An aligned legacy store can use the procedure with an empty
list of entries ahead of the anchor. Legacy stores have no stored links for the
unanchored suffix; the owner must reconcile the displayed entries against the
original request. The command does not reset the registry or release identities.
Injected crash tests cover the commit/anchor window; power-loss recovery on
physical storage remains unqualified.

A caller controlling both the evaluator environment and anchor can replace the
whole registry. Separate-identity deployment and protection of that anchor remain
operator obligations, not guarantees of an in-process Python API. Trust bytes
remain externally authenticated and frozen by the owner. Hashes establish
consistency, not provenance or physical measurement truth. Only the synthetic
resource-count profile is admitted; no result authorizes execution, continuation
or genuine-data acceptance. `_assess_records` remains an internal consistency
calculator for chronology tests; consumers must use `assess`.
