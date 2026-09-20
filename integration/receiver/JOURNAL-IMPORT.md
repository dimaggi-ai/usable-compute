# Synthetic TENWA journal import

`dimaggi_receiver.journal_import` imports TENWA's
`dimaggi-batch-journal-export/v1` into the existing application
[`ObservationStore`](OBSERVATIONS.md). It preserves source history for one
registered request. It creates no authority ledger, evaluates no policy and
performs no workload action. Every imported event remains **synthetic**.

The source implementation is TENWA's `internal/batchjournal`, using the existing
Core v0.3.0 preview. This adapter supports only exports whose complete history
uses the closed `synthetic-lab-test` contract. Model-only records and journals
mixing model and lab records refuse. Real scheduler observations, production
permissions and future wire versions require separate reviewed adapters.

## Configuration and identity

The caller registers the application intent and its three read sources before
importing. The importer never derives a new approved intent from the input file.
`expected_journal_id` must come from the caller's configuration; matching it is
an attribution check, not authentication. The source journal persists this ID.

| Application field | Exact source value |
|---|---|
| `request_id` | Snapshot `request_id`, also equal to input and preview request IDs |
| `report_id` | `input.report_binding.report_id` |
| `profile_id` | `input.report_binding.profile_id` |
| `target_id` | `input.report_binding.target_scope`, a synthetic scope |
| `workload_id` | `input.report_binding.selected_request`, the logical workload identity |
| Permission source | `journal_id + "/permission"` |
| Attempt source | `journal_id + "/attempt"` |
| Workload source | Independently configured by the application; this importer emits no workload event |
| Evidence class | `synthetic`, for the registered intent and every imported event |

The application's `desired_state` remains its own immutable choice. The source
attempt ID is the attempt stream identity; the request ID is the permission
stream identity. Synthetic operation and object IDs retain the source's exact
digest-derived values. Logical workload identity and immutable object identity
remain separate.

The API is:

```python
import_journal(
    store,
    export_json,
    request_id=request_id,
    expected_journal_id=journal_id,
    recorded_at_utc=collection_time,
)
```

`export_json` is JSON text or UTF-8 bytes. `journal_events` exposes the same
validation and conversion without appending; its `intent` argument must be an
already validated application intent. The caller supplies collection time in
UTC with `Z` and at most microsecond precision. No clock is inferred or refreshed
from file modification times.

## Source-to-application meanings

| Source record | Application read state | Meaning |
|---|---|---|
| Initial permission `not_granted` | `permission: unresolved` | No permission exists in this simulation; the recorded Core verdict is preserved separately |
| `prepared` | `attempt: not_started` | Persisted synthetic reservation; permission remains ungranted |
| `not_sent` | `attempt: not_started` | Source-reported pre-entry refusal or expiry, retained in `payload.source_state` and the full snapshot |
| `dispatching` | `attempt: unknown` | Conservative read of the synthetic uncertainty interval |
| `unknown` | `attempt: unknown` | Unresolved source attempt; absence does not establish no effects |
| `acknowledged` | `attempt: submitted` | Attributed synthetic object acknowledgement, never workload success |

The permission event is emitted once, at reservation. Every historical attempt
transition is emitted. Original source times, source epoch, source sequence,
record digest, exact input, report bytes, policy preview and observation survive
inside `payload.source_record`. `source_version` is the opaque record digest;
`source_sequence` is the source's integer sequence, starting at 2 because its
header occupies sequence 1. Only source epoch `"1"` is supported.

The adapter checks exact transport fields and types, contiguous full-history
sequences, monotonic source times, immutable request bindings, source state and
reason compatibility, and object attribution. The report's SHA-256 binds its
decoded exact bytes. An allowed recorded verdict must be consistent with its
expected binding, synthetic approval and finite expiry; dispatch entry requires
time strictly before expiry. These are source-record consistency checks. Core
owns policy evaluation. Its envelope and policy records are retained as opaque
finite JSON except for the recorded decision enum and the consistency checks
just described; their other fields are not reinterpreted by the application.

The Go record digest is preserved without recomputing it using Python's different
JSON serialization. This import does **not** verify the source audit chain,
authenticate the writer, prove the exported history is complete, or establish
that source claims are true. A valid-looking export can be forged. Expected
source IDs, hashes and strict schemas do not change that limitation. Source-side
journal verification and future authenticated delivery remain separate concerns.

## Persistence, freshness and limits

All export validation and collection-time checks finish before the first append.
Each append then uses the existing store's idempotency and durable conflict rules.
An I/O failure or conflict against previously imported evidence can leave an
accepted prefix; importing the same export again is safe. There is no second
batch transaction or authority store. The existing single-writer and filesystem
limits in [OBSERVATIONS.md](OBSERVATIONS.md) still apply.

Duplicate import leaves both source observation and first collection times
unchanged. Freshness remains the application's explicit per-role policy; stale
source evidence stays stale. Importing an older export prefix never deletes a
previously observed transition. A source absence observation stays in the attempt
payload and never becomes a fabricated workload observation. Separately imported
workload absence also leaves unknown attempt effects unresolved.

The receiver's existing strict JSON boundary limits input to **4 MiB** and 64
nesting levels. The source journal can grow beyond this transport limit; oversized
exports refuse completely. Paging, truncation and partial-history imports are
unsupported. The importer does not widen the receiver's limit silently.

The returned summary always reports `permission: not_granted`,
`dispatch_possible: false`, `execution_proven: false` and
`workload_outcome: not_observed`. Application reconciliation may remove a
particular missing-evidence condition as records arrive, but neither import nor
case closure grants an action, resolves source authority, or triggers a retry.

## Verification

Run the synthetic adapter tests from the research repository:

```sh
PYTHONPATH=integration/receiver/src python -m pytest -q integration/receiver/tests/test_journal_import.py
```

These cases cover restart and duplicate import, stale evidence, unknown attempts
followed by absence, exact expiry, changed bindings, schema and numeric errors,
object attribution, contradictory source claims, and immutable history. The
cross-repository integration tests separately exercise exports from the actual
compiled Go command. Neither test family is executor or operator proof.

Independent review reproduced and corrected five importer consistency gaps:
an observation predating the previous transition; an allowed verdict without an
expected binding; a boolean accepted as preview cardinality through Python
equality; an incompatible freeform transition reason; and an allowed verdict
paired with invalid execution readiness. Tests now refuse each case. All
execution and permission flags were false in those probes.

This adapter is product integration work. It does not add RSI execution or count
toward the read-only digest's manual usefulness baseline.
