# First-slice offline receiver

This installable application calls Maggie's existing domain implementations and
maps their actual results into `dimaggi-receiver-report/v1`. The selected request
is `job-0`; the cohort ledger is context. Every report has
`execution_readiness: incomplete`, `execution_authorized: false` and
`mutation_request: null`. It has no scheduler client, credential path or dispatch
function. Model, future CPU-executor and future operator proof remain separate.

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
`DIMAGGI_TEST_SOURCES`; those skips do not establish model acceptance. Tests cover
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

The installed-receiver GitHub workflow builds a wheel on macOS, fetches the
exact eight public source commits, verifies their locked file hashes and runs
the receiver/domain tests with no unexpected skips. It explicitly excludes the
two TENWA subprocess suites, which require separately supplied binaries. Run
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

The complete local acceptance suite also runs `test_collection_roundtrip.py` with the actual TENWA object verifier and payload binaries. The public installed-wheel workflow explicitly excludes that file together with the two existing TENWA subprocess suites; it runs the collector's local TLS and independent adversarial tests. The release reproduction script supplies all three binaries and fails if required tests skip.
