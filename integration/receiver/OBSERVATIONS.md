# Offline workload observations and divergence

`dimaggi_receiver.observations.ObservationStore` is Ronnie's bounded local R7
component. It persists application workload intent, imported read projections,
divergence snapshots and triage notes in an existing receiver package. It has no
credentials, network client, scheduler write, Tool Guard verdict implementation,
TENWA attempt writer, retry, cleanup or provisioning path.

The tests generate **synthetic** records. They establish journal/adapter behavior,
not a real submission, authenticated source, executor proof or operator outcome.
Live observation collection and execution-boundary integration remain pending.

## Authority and identity

The application owns `register_intent`, reconciliation snapshots and triage notes.
`register_source` declares one configured source for each imported role; it does
not authenticate it. Every event keeps the source ID, source epoch, source record,
opaque source version, observation time, collection time and identity binding.

| Record | Owner represented by imported evidence | Meaning |
|---|---|---|
| `permission` | Dmitry's supported authority boundary | Source-reported allowed/denied/unresolved/expired; an allowed read projection grants no permission here |
| `attempt` | Dmitry's execution boundary | Separate logical attempt ID, status and attributed immutable object ID when acknowledged |
| `workload` | Designated scheduler/controller via configured observer | Pending/running/succeeded/failed/absent/deleted/unknown for one declared workload identity |
| Intent, divergence, triage | Ronnie's application | Desired state, conflicting evidence references and hold/escalate/proposal; never authority state |

`request_id`, `report_id`, `profile_id`, `target_id` and `workload_id` bind each
event exactly to its immutable registered intent. The workload ID is the logical
scoped name; `object_id` is the actual immutable instance identity. A second UID
at the same logical name is explicit divergence, even after deletion. An observed
object without an attributed acknowledged submission also requires escalation.
Within this journal, a source stream cannot move between request bindings, and
one target's immutable object cannot be attributed to two logical requests.
Conflicting cross-request attribution refuses and leaves a durable conflict;
resolution needs the external identity/reconciliation contract.
Report IDs are opaque bindings here; the report module owns hash validation.
The schema's identity names are an application read contract, not invented fields
in a Tool Guard or TENWA wire contract. Supported source adapters must map them.

The caller must supply `source_sequence`: a nonnegative integer that is monotonic
within `(source_id, source_epoch, source_record_id)`. `source_version` remains
opaque. Do not parse or compare an arbitrary scheduler resourceVersion as an
integer. If the source does not provide ordering, its adapter must establish and
document a trustworthy collection sequence/epoch; this module cannot infer it.
Permission records use the request ID as `source_record_id`, workload records use
the workload ID, and attempt records use their distinct attempt IDs. All attempts
remain visible; a later attempt cannot erase an earlier failed/unknown one.

## Storage and calls

Only the standard library is needed. One active `ObservationStore` owns a file
journal, including reads through that store. A second store is refused before
it opens SQLite, including a separate process using a symlink or hard link to
the same inode. This enforces the bounded one-writer precondition; it does not
provide a multi-writer reconciliation algorithm. Close the store before handing
it to a new process. `:memory:` instances remain independent, nonpersistent stores.

**File-backed platform support is deliberately narrowed:** this increment was
tested on macOS ARM64, CPython 3.12.6, using Darwin's `F_OFD_SETLK`. Other platforms
fail closed for file journals until their locking implementation is verified;
portable report generation is unaffected. The old implementation used portable
SQLite calls but did not enforce its single-writer requirement. Use a regular
file on a local filesystem; network filesystems, alternate VFS implementations,
forking with an open store, and renaming/replacing its file while open are not
supported or verified. The API does not certify filesystem locality.

The advisory open-file-description lock covers byte 0 of the actual database
inode and ends when its descriptor closes or the process exits. Its Darwin
`struct flock` layout comes from the available SDK's `sys/fcntl.h`
(`off_t`, `off_t`, `pid_t`, `short`, `short`), rather than a guessed Linux ABI.
SQLite reserves a separate [lock-byte region beginning at 1 GiB](https://www.sqlite.org/fileformat2.html#the_lock_byte_page).
Tests cover crash release, failed initialization, aliases, and closing other
file/SQLite descriptors without releasing ownership. Use a canonical database
path for recovery and its SQLite journal files; alias contention tests do not
establish safe recovery through a different hard-link name.

An initial whole-file `flock` implementation failed on this platform because it
interfered with SQLite initialization (`database is locked`). It was replaced by
the tested OFD byte-range lock. Process-owned POSIX record locks were rejected
because closing another descriptor can release them. Advisory locks and triggers
are programming guards, not security boundaries against raw SQL or a process
that can modify the database file. Tamper resistance, signatures, retention and
general schema migrations remain outside scope. SQLite transactions protect
individual appends; triggers reject UPDATE/DELETE of every journal table.
Cross-request object checks scan this bounded local journal; large-scale ingestion
and indexing are not claimed or benchmarked.

1. Open `ObservationStore(path)` and register the three read sources using
   `register_source(source_id, kind, target_id)`.
2. `register_intent(intent)` persists all five identity fields, `desired_state`
   (`present` or `succeeded`), the three `sources`, and `evidence_class`
   (`synthetic` or `observed`). Exact repeat is a no-op; changed reuse refuses.
3. `append(event, recorded_at_utc=...)` stores source evidence. The exact required
   keys are exposed as `EVENT_KEYS`. Payload is finite JSON; no unit conversions
   or lifecycle claims are derived from arbitrary payload keys. If supplied,
   `payload.foreign_change` is an explicit boolean assertion by the observer.
4. `project(request_id, as_of_utc=..., freshness_seconds=...)` reads the journal.
   `reconcile` returns the same read model plus an immutable reconciliation ID and
   persists it. Reopening the database retains identities, history and snapshots.
5. `record_triage(triage_id=..., reconciliation_id=..., case_id=..., actor_id=...,
   disposition=..., reason=..., recorded_at_utc=...)` appends application triage.
   Allowed dispositions are `hold`, `escalate`, and `proposal`; a proposal is
   only a reviewer note. `triage_history(case_id)` retains every note and author.
6. `record_resolution(resolution_id=..., opening_reconciliation_id=...,
   resolving_reconciliation_id=..., case_id=..., actor_id=..., reason=...,
   recorded_at_utc=...)` records only `resolved_in_read_projection`.
   `resolution_history(case_id)` reads those immutable application notes. Exact
   repeats are no-ops after restart; changing any argument under an existing
   resolution ID refuses. Actor identity is attribution, not authentication.

No default freshness is supplied. `freshness_seconds` must explicitly contain
positive finite seconds for all three roles. UTC timestamps require `Z` and at
most microseconds. A projection includes only records collected by its as-of
time, expires freshness at equality, and preserves source state separately from
freshness. A fresh imported `allowed` status is not an approval-lifetime check.
An action adapter must independently enforce the actual bound finite expiry.

Transport duplicates with equal content are idempotent. Reused event identities,
source record/version or sequence with different content refuse and append a
durable conflict record. The previous source event is never overwritten. Late
events are retained and labeled; sequence order chooses the latest within an
epoch. A new epoch produces `source_reset` and retains both epochs. **Refreshing
timestamps cannot clear source reset or conflict cases.** No epoch-selection,
conflict-close or permission-override operation exists in this bounded component.

Case IDs bind the request, reason and cited evidence IDs. Snapshots preserve
evidence references, first-supported time, age and disposition. Stale-case age
starts at expiry or later ingestion, not the original observation. For a TTL
below timestamp precision, first support is the first representable microsecond
at or after expiry; the exact freshness comparison still uses the declared TTL.
Missing evidence has unknown (`null`) first-supported time and age because no
observed start time exists; repeated projections do not invent an age of zero.
Triage does not change the computed evidence disposition, erase an unknown
attempt, resolve effect uncertainty or produce a mutation request.

## Bounded application resolution

Resolution notes have a deliberately narrow meaning: a named application evidence
condition is absent from a later persisted read projection. They do not rewrite
or suppress computed cases. Both snapshots must bind the identical intent and
exact freshness policy, the resolving snapshot must be later, and the note
cannot precede it. The relevant source projection must be current. The module
also recomputes the projection at note time, refusing an old clear snapshot if
the condition has recurred, evidence has expired, or a conflict has since arrived.

The allowlist is `permission_missing`, `attempt_missing`, `workload_missing`,
the corresponding three `_stale` conditions, `workload_unknown`,
`desired_outcome_not_met`, and `desired_observed_divergence`. A replacement event
can change a case ID while leaving the same semantic condition active; therefore
the check uses the reason and its source scope in the same immutable intent,
not merely disappearance of the old case ID. Every current stream of that source
must satisfy freshness. Neither source resets/conflicts nor object-identity or
foreign-writer issues may be present in the resolving or note-time projection.

Clearing one evidence condition does not mean the request is healthy: other
conditions remain computed independently. In particular, a newly observed
permission record can clear `permission_missing` while `attempt_unknown` still
requires escalation. Every resolution records `mutation_request: null`,
`grants_permission: false`, and `resolves_attempt_effects: false`. Raw events,
unknown attempts, triage and previous snapshots remain unchanged. Later
recurrence or conflict is an active case even if an earlier resolution note
exists. A historical resolution is neither a permanent suppression nor a claim
that the scheduler's source state is authoritative or correct.

Selecting a source epoch, closing source conflicts, resolving unknown attempt
effects, and changing permission remain unsupported. Those operations need
their source/authority contracts; an application reviewer cannot manufacture
them. The additional `resolutions` table is append-only and leaves prior records
and serialized snapshots unchanged.

## Required interpretations and tested limits

| Situation | Required result |
|---|---|
| Submission completed; workload failed | Both records retained; desired successful outcome unmet, not a contradictory-authority claim |
| Permission allowed; attempt unknown; later object absence | All three facts remain; escalate unknown effects; no blind resubmit |
| Out-of-band deletion | Desired/observed divergence; no assumption that a controller recreates the Job |
| Duplicate after process restart | Existing logical source event and reconciliation reused |
| Changed report/profile/target/workload/source | Reject the foreign binding; intent remains unchanged |
| Logical name reused for another UID | Preserve both histories and escalate identity reuse |
| Missing, stale or unknown | Separate labels; neither becomes zero, success or no effect |
| Foreign-writer assertion | Retain source assertion and escalate; attribution is not authentication |
| Source version conflict or source reset | Sticky explicit conflict/reset, with all accepted earlier evidence retained |
| Old stale case ID vanishes but replacement evidence is still stale | Resolution refused; the semantic condition remains active |
| A prior application resolution followed by stale evidence or conflict | Current case remains active; prior resolution stays historical |
| Current successful observation | `retain`; still no authorization, mutation request or executor-proof claim |

Delivery-Decisions' real eight-case executor acceptance set is **not** completed
by these tests. This component exercises only offline persistence/observation
semantics relevant to that set. Actual API identity, credential isolation,
real retries, process/network failure around submission, restart across the
execution boundary and actual observation freshness require the separately
supported executor/observer integration.

Run the synthetic tests from the research repository with an installed receiver,
or with its source directory on `PYTHONPATH`:

```sh
PYTHONPATH=integration/receiver/src python -m pytest -q integration/receiver/tests/test_observations.py integration/receiver/tests/test_observation_resolution.py
```

These product observations/cases are not RSI work. No candidate execution or
read-only digest improvement claim is produced here.
