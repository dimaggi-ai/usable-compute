> **Infrastructure integration:** receiver 0.1.8 adds provider telemetry, joined admission,
> persistent source review and reproducible local transport-failure campaigns.
> See [operations and replay commands](OPERATIONS.md).
> Start with [the integrated workflow](INFRASTRUCTURE.md) and [delivery assessment](DELIVERY-ASSESSMENT.md).

# First-slice offline receiver

This installable application calls Maggie's existing domain implementations and
maps their actual results into `dimaggi-receiver-report/v1`. The selected request
is `job-0`; the cohort ledger is context. Every report has
`execution_readiness: incomplete`, `execution_authorized: false` and
`mutation_request: null`. The model/report commands remain offline and do not dispatch. Explicit telemetry
collection commands use read-only provider APIs; governed CPU dispatch stays in
TENWA. Model, workload observation and operator proof remain separate.

The receiver lives in the existing research repository. It adds transport,
source binding, report mapping and observation projection; physical models,
margins, scope requirements and synthetic generators remain in their owners.
The committed Maggie profile is packaged byte-for-byte and checked against its
locked source. A request cannot select another profile or source lock.

## Install and export sources

The package declares Python 3.11 or later. The reproducible recipe below is
verified for Python 3.12 on macOS arm64;
`requirements-darwin-arm64-py312.lock` binds the wheels used for that environment.
Other interpreter/platform combinations require their own wheel lock and tests.
Version 0.1.1 enforces one active file-journal writer with Darwin OFD locks;
file-backed observations now refuse unsupported platforms before creating a
journal. In-memory observations and the model/report path do not require that
lock. This narrows file-journal compatibility; see [the tested locking limits](OBSERVATIONS.md).

```sh
python3 -m venv /tmp/receiver-env
python3 -m pip download --only-binary=:all: --require-hashes \
  -r integration/receiver/requirements-darwin-arm64-py312.lock \
  --dest /tmp/receiver-wheels
/tmp/receiver-env/bin/python -m pip install --no-index \
  --find-links /tmp/receiver-wheels --require-hashes \
  -r integration/receiver/requirements-darwin-arm64-py312.lock
/tmp/receiver-env/bin/python -m pip install --no-index --no-deps \
  --no-build-isolation ./integration/receiver
python3 integration/receiver/tools/export_sources.py \
  --source-map /tmp/receiver-source-map.json --out /tmp/receiver-sources
```

The local source map is a JSON object whose keys are the eight logical names in
[`sources.lock.json`](src/dimaggi_receiver/sources.lock.json), and whose values
are existing local Git checkouts containing those exact commits. Keep local
paths out of published/committed artifacts. The exporter uses Git objects, so
working edits are neither discarded nor exported. It refuses an existing output
directory. The exported bundle can move to another directory or machine; it does
not need Git, network access or the original workspace at runtime. Existing
license notices are retained.

The lock selects the original `usable-compute` joined source and reviewed
cooling, resource, calibration and checkpoint corrections. It does **not** claim
all eight repositories are the original audit pins. Each report records all
selected revisions, file-set digests, actual imported file hashes and runtime
versions. The corrected cooling implementation is imported directly by the
original joined composition; no cooling algorithm is copied into this package.

Verification checks every locked file, rejects symlinks and unexpected files
(including importable bytecode/native shadows), and loads owner code in a fresh
`python -I -B` process. `PYTHONPATH` and a caller's already imported packages do
not choose model implementations. Local hashes do not prove authenticity,
physical truth or permission; exported source directories must remain under the
operator's control during a run.

## Run the actual receiver

```sh
/tmp/receiver-env/bin/dimaggi-receiver verify-sources --sources /tmp/receiver-sources
/tmp/receiver-env/bin/dimaggi-receiver model --sources /tmp/receiver-sources \
  --case baseline > /tmp/receiver-baseline.json
/tmp/receiver-env/bin/dimaggi-receiver model --sources /tmp/receiver-sources \
  --case restore_geometry > /tmp/receiver-healthy.json
/tmp/receiver-env/bin/dimaggi-receiver exercise --sources /tmp/receiver-sources \
  > /tmp/receiver-cases.json
/tmp/receiver-env/bin/dimaggi-receiver policy-input \
  --report /tmp/receiver-baseline.json --request-id receiver-review-0 \
  > /tmp/receiver-preview-input.json
```

The model command accepts only the reviewed named cases, all at seed 0. An
unknown case or arbitrary input override is an error; it cannot silently create
a different workload/topology. The names select actual calls to
`usable_capacity.scenario`, not predefined report answers.

| Case | Actual model meaning | Expected receiver result |
|---|---|---|
| `baseline` | Fragmented torus; cooling admits but no rectangle | Refused; wait; zero useful compute |
| `wider_network` | Same geometry with network-efficiency intervention | Refused; wait; zero cohort gain |
| `restore_geometry` | Healthy geometry; 128-chip request | Feasible within model; execution incomplete |
| `restore_and_network` | Healthy geometry and network intervention | Feasible within model; cohort gain is not job gain |
| `insufficient_power` | Healthy geometry, restrictive feed | Cooling refuses; downstream checks not run |
| `smaller_gang` | 64-chip geometry works but rough memory exceeds configured memory | Refused; workload-incomparable; no gain value |
| `no_change` | Identical healthy candidate and baseline | Retain current state; no action |

Raw baseline/candidate, selected cooling result and rough GPU-memory quantities
remain in `evidence.raw`. Check states include source, scope, reason and immutable
model provenance. Unrun downstream checks remain visible next to a geometry or
power failure. Operator CPU, host/pinned RAM, runtime GPU peak, storage, I/O,
plant and policy quantities remain unknown. No allocator or resource margin is
inferred from the nominal GPU count.

The four joined buckets retain GPU-hours and their domain meanings. Checkpoint
blocking is a separate GPU-second ledger and is never added/subtracted from the
joined recovery ledger. The comparison requires the same workload, seed, window,
denominator and failure assumptions with shared sources/runtime; only the named
interventions differ. A 64-chip substitution has a null gain. The rough memory
screen can reject but cannot establish runtime fit.

## Domain adapters and negative cases

`exercise` reuses the owners' deterministic fixture generators, then executes the
owning functions for every input. It adds adapter-boundary mutations such as a
missing required argument, boolean quantity, changed binding or omitted scope.
All cases are exposed regressions, including owner fixtures with historic
`held_out_acceptance` labels. They are not a protected holdout or observed data.

| Adapter kind | Owning entry point | Meanings retained |
|---|---|---|
| `capacity` | scheduler `capacity.resource_evidence.capacity_check` | Integer bytes, CPU units, per-scope demand/capacity/margin and each input state |
| `checkpoint_bound` | scheduler `checkpoint_bound_check` | A favorable lower bound remains incomplete |
| `coverage` | scheduler `capacity.resource_coverage.evaluate_coverage` | Explicit required scopes, missing components, known shortfalls and uncovered domains |
| `calibration` | scheduler `capacity.calibration_evidence.compare_pair` | Matched context, unit/statistic, time validity and supplied tolerance |
| `checkpoint_phases` | reliability `evaluate_checkpoint` | Save/shard/commit/restore identities, gaps and negative outcomes |
| `checkpoint_ledger` | reliability `blocked_ledger` | Declared full-gang intervals; recovery wins overlap; unknown coverage gives null buckets |
| `span` | span `SpanEnvelope.from_dict`, `validate`, `audit_record` | Raw decision, findings, policy and `not_checked` |

Evaluate supplied offline input using
`dimaggi-receiver evaluate --sources BUNDLE --input INPUT.json`, where input is
exactly `{"kind": "capacity", "input": { ...owner arguments... }}`. Omitting
`--input` reads stdin. No owner algorithm or floating comparison is recomputed
from rounded display output. A calibration `pass` means only within the supplied
tolerance; a coverage `pass` means only complete declared resource checks.
Neither is executable readiness. Raw quantity evidence labels are preserved as
supplied labels, not authenticated observations. Owner exceptions become an
`invalid` result with original exception type/message; missing, stale and
not-run states remain distinct in the raw result.

Strict JSON refuses duplicate keys, non-finite numbers (including exponent
overflow), unpaired Unicode surrogates in keys or values, inputs over 4 MiB and
nesting over 64 levels. Valid non-BMP characters and literal U+FFFD retain distinct
identities through the Python/Go boundary. Unknown/error is never
coerced into zero. Malformed JSON or domain input returns exit code 2 with a JSON
error; command-line syntax errors use argparse's usage message.

## Report/stub binding and observations

`report_id` is a local semantic digest over canonical report content excluding
itself. `policy-input` additionally hashes the **exact report file bytes** into
`report_binding.report_digest`, avoiding cross-language float canonicalization.
The preview carries the original action intent, selected request, profile/evidence
digests and null target/payload. Approval is `not_requested`, expiry is unknown
and `expected_binding` is null until a receiver explicitly provides a reviewed
binding. Generating the preview does not supply independent reviewer approval.

The existing `tenwa-ant` `cmd/batch-preview` consumes that non-executing preview
against its supported Tool Guard Core version. Its output is policy preview
evidence only; it is neither a permission grant nor an execution receipt.

`validate_report` reconstructs the entire presentation from raw model results,
the trusted profile and pinned source metadata. Rehashing an inconsistent
recommendation, gain, bucket, unit, scope, provenance claim or missing mandatory
check does not make it valid. It cannot authenticate externally supplied raw
model results; obtaining fresh model evidence requires rerunning the receiver.

[`OBSERVATIONS.md`](OBSERVATIONS.md) describes the independent durable read
projection for report/request/permission/attempt/workload IDs and divergence.
It records attributed observations; it cannot submit, approve, retry or cancel.

Version 0.1.2 adds [the synthetic journal importer](JOURNAL-IMPORT.md). It consumes
the complete bounded TENWA export for a preconfigured synthetic intent, preserves
attempt history and source times, and imports permission as unresolved. It does
not create workload observations, authenticate a journal or grant execution.

The same release adds [installed observation commands](OBSERVATION-CLI.md) for
explicit initialization, import, projection, reconciliation and review notes.
The [offline Kubernetes adapter](KUBERNETES-IMPORT.md) retains attributed Job and
Pod fixtures, including uncertain and conflicting lifecycle evidence. It has no
API client and accepts synthetic evidence only. Neither an imported terminal
state nor a persisted review note proves that a workload actually ran.

## Verify

Install the receiver into the test interpreter so the isolated child can resolve
the same package, then use an exported bundle:

```sh
DIMAGGI_TEST_SOURCES=/tmp/receiver-sources \
  DIMAGGI_BATCH_PREVIEW=/tmp/dimaggi-batch-preview \
  DIMAGGI_BATCH_JOURNAL=/tmp/dimaggi-batch-journal-demo \
  /tmp/receiver-env/bin/python -m pytest integration/receiver/tests -q
```

Build `/tmp/dimaggi-batch-preview` from the supported TENWA source as described
in its `docs/batch-preview.md`. Cross-language tests explicitly skip without
`DIMAGGI_BATCH_PREVIEW`; a run with those skips is not report/stub acceptance.
Build `batch-journal-demo` from the same TENWA checkout. The journal round-trip
tests require `DIMAGGI_BATCH_JOURNAL`; skips do not establish journal integration.
Install pytest separately (the recorded environment lock includes it); it is not
a runtime dependency. Tests requiring the bundle explicitly skip without
`DIMAGGI_TEST_SOURCES`; those skips do not establish model acceptance. CI sets
`DIMAGGI_EXPECT_SOURCES=1`, which makes a missing bundle or an ordinary skip fail. Tests cover
all seven real model cases, owner fixture round trips, exact numeric decisions,
byte preservation, forged/rehashed report variants, source tampering/import
shadowing and strict CLI boundaries. The separate operator-discovery, CPU lab
execution and production authorization gates remain unfulfilled by these tests.

Version 0.1.3 bounds file and stdin reads before parsing for `evaluate` and
`policy-input`, sharing the same 4 MiB byte limit with observation imports.
File inputs must be regular files; named pipes and devices are refused without
waiting for a writer. Standard input remains a supported streaming source for
`evaluate`; its read waits for EOF or the byte bound and has no wall-clock
arrival deadline. Invalid or oversized input produces a structured refusal
before domain evaluation. The byte limit does not bound total Python process
memory or domain evaluation cost. Large journal exports still require a separate
bounded transport design; this change does not add pagination or truncate them.

The installed-receiver GitHub workflow is configured to build on macOS and both
Linux architectures, export the same eight pinned source commits, verify their
file hashes and run source-bound tests. The JUnit gate allows exactly the named
strict simulator xfail while its old commit remains pinned; every other skip
refuses. Pytest defaults to strict xfail; the report hook also turns explicitly
non-strict XPASS into a failing exit. The gate requires the hook's policy marker,
rejects detectable `wasxfail`/XPASS representations, and requires every test
declared in `tools/critical_tests.json` to appear exactly once. Each must pass,
except for the sole simulator case at the old pin, which must have the named
xfail. A second copy of that identity refuses at every pin.

The gate also requires the complete JUnit case set to equal the committed
`tools/receiver_tests.json` manifest: every expected identity exactly once, with
no extras. Suite counters for tests, skips, failures and errors must match the
actual cases. The evidence workflow gates all root `tools` tests, including RSI,
against `tools/tools_tests.json` with no allowed exceptions. Both manifests are
produced by `pytest --collect-only` over the workflow selections. After an intended
test-selection change, run `python integration/receiver/tools/ci_test_manifest.py`
from the repository root with the source bundle configured. Use `--check` to
verify without writing; staleness tests also compare current collection to each
manifest.
JUnit alone cannot identify XPASS if an external producer strips its marker and
reports an ordinary pass; the policy marker is evidence from the configured
runner, not authentication of arbitrary XML.

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

See [gate operation and limits](tools/README.md).

Four TENWA subprocess suites require separately supplied binaries and
are excluded from these public jobs. Local gate checks do not establish a
successful hosted run or native platform qualification. Run
those separately with the documented
TENWA binaries for cross-language acceptance. CI success does not establish
another engineer's independent acceptance or real scheduler execution.

Version 0.1.4 also supports 64-bit Darwin Python builds that omit the
`F_OFD_SETLK` name: it uses command 90 from Apple's
[published XNU ABI](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/fcntl.h).
The same kernel OFD lock must succeed before SQLite opens; there is no fallback
to an unlocked or process-scoped writer. Tests remove Python's constant, verify
competing descriptors are refused even after an unrelated descriptor closes,
and require unsupported kernel calls to fail before SQLite initialization.
Other operating systems remain outside file-journal support.

## Explicit read-only collection library

Version 0.1.5 adds the opt-in [TLS collection library](KUBERNETES-COLLECT.md). A trusted host supplies the target, public CA, in-memory credential, pinned Job identity and hashed TENWA verifier. This path reads Kubernetes resources and bounded output into the existing observation journal. Existing file-import and model CLI paths retain their original scope; arbitrary input cannot enable network collection. The collector grants no execution permission or automatic retry.

The complete local acceptance suite also runs `test_collection_roundtrip.py` with the actual TENWA object verifier and payload binaries. The public installed-wheel workflow explicitly excludes that file together with the policy, journal and infrastructure-binding TENWA subprocess suites; it runs the collector's local TLS and independent adversarial tests. The historical release reproduction script retains its original three interfaces. The current [combined verification command](INFRASTRUCTURE.md#combined-acceptance-automation) also supplies the infrastructure planner interface and refuses skipped tests.

### Explicit local Kubernetes Pod compatibility (0.1.6)

The read-only collector accepts omitted per-item type metadata only inside a
validated v1 PodList and records its original byte digest. Actual local v1.35.0
collection exposed this typed-list representation and a narrow set of Pod-only
admission defaults. An explicit `pod_profile` opt-in supports exactly the stock
priority/preemption/toleration values; other mutations remain unverified. The
original strict default remains available. See the [collector contract](KUBERNETES-COLLECT.md)
and [Mac invocation harness](tools/COLLECT-LAB.md). This compatibility does not
grant workload execution permission or replace operator acceptance.

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
fixture used 7,434,240 bytes per initial publication in an earlier local run;
three such retained copies per 30 seconds add 22,302,720 bytes per cycle.
Size varies with database layout and SQLite growth. Monitor
free space and open deleted files with `lsof +L1`. Every commit copies the entire
database; tmpfs timings are not durable-disk guarantees. Use distinct service
identities and protect the parent directory. See the topology collection profile
for the proof, failure semantics, migration and clock assumptions.

A provisioning fsync failure is an error even if the leftover ledger header
parses. Do not adopt that residue as durably provisioned storage. Local source
and gate results do not establish installed, crash, power-loss or physical
guarantees.
