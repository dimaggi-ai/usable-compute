# First bounded decision reference

The pinned loader rejects already cached domain packages from a different source
tree. Run it in a fresh Python process. The combined pytest suite scopes and
restores its package cache so unrelated working code cannot silently
replace the selected source baseline. Source hashes in earlier strategy
packages describe their earlier run; new runs bind the current reference bytes.

Maggie's product-domain reference, 19 September 2026. Synthetic/model evidence
only; no scheduler access or execution permission. This extends the existing
joined experiment with a capability/evidence profile and known-answer cases.
It does not change the model algorithms; cooling input validation comes from
the selected cooling source revision.

- `profile.json`: proposed `maggie-capability-evidence/v0.1-proposed` profile;
  operator and CPU-executor values remain separate and unknown.
- `cases.json`: frozen numerical answers and evidence-state cases, with splits.
- `reference.py`: calls the pinned models, reuses netcap's rough memory screen,
  checks the cooling refusal contract and writes local reproduction/provenance records.
- `test_first_decision.py`: independent arithmetic, model and boundary checks.

From this repository root, using Python with the dependencies below and seven
fresh, clean sibling checkouts at `reference.PINS`:

```sh
export MAGGIE_REPOS_ROOT=/path/to/fresh-pinned-siblings
python integration/first_decision/reference.py \
  --repos-root "$MAGGIE_REPOS_ROOT" --out /path/to/new-evidence-directory
python -m pytest integration/first_decision -q
```

The first-decision CI workflow creates these checkouts from the exact commits.
Preserve historical audit checkouts; their old cooling pin is intentionally
incompatible with this candidate. Pass `--repos-root` and set `MAGGIE_REPOS_ROOT`
for pytest to the directory of the seven named pinned siblings. The runner requires
the exact seven commits in `reference.PINS` and no tracked changes there. It
records tracked Python/config/schema file hashes and Python/package versions.
Do not overwrite an earlier evidence directory when comparing implementations.
Dependencies are Python 3.12, NumPy, PyYAML, jsonschema and pytest; the actual
reproduction versions are recorded in `provenance.json`. Ronnie owns a portable
locked install recipe; this reference deliberately uses the available audit
environment. It requires no network or installation when that environment exists.

The original report retains the original field names. `useful_compute_gpu_h`
means modeled retained compute service, including recompute. Its complement
contains synchronization wait as well as communication and pipeline bubbles.
No completed-work, training-quality, GPU performance or ROI claim follows.

`classify` is a small executable meaning reference for already-supplied check
states. It neither establishes those states nor verifies their authenticity,
timestamps, target binding or live applicability. The JSON profile is descriptive
and proposed, not a released wire schema or production authorization validator.
All references emit `mutation_request: null`, even when model checks pass.

The cooling regression requires `ValueError` at the model boundary for NaN and
both infinities in hall feed, feeder ceiling, feeder step and required ride-through.
The probe records these input failures as `REFUSE`, preserves finite physical
constraint failures as `DENY`, and continues to a valid `ADMIT` control. Unexpected
programming exceptions still propagate. A plain green assertion of the old
NaN-ADMIT defect would reward retaining the bug; these rejection assertions fail
on the defective source and pass on the corrected source.

The selected cooling main commit is
`538314a785e8676a63b4a37aa4b9f9c22382ab93`, merged on 19 October 2026 through
[cooling PR #1](https://github.com/dimaggi-ai/cooling-pue-ladder/pull/1). Its tree
is identical to the independently tested correction `735d8eb8`; the receiver's
separate source lock retains that original exact source. The first-decision
reference now records the merged main commit required by task #53. This satisfies
the upstream landing prerequisite; independent acceptance of this receiver
candidate remains separate. Earlier evidence remains tied to its original source
hashes and must not be overwritten.
The 64-chip negative case reuses the existing rough memory model and rejects
90.707679216 GB against the modeled 80 GB limit. A 128-chip screen pass does not
establish runtime memory fit or any host/storage feasibility.

No additional stochastic generator was needed. Existing seeded recovery and
scenario variants supply the modeled phenomena. Contract fixtures add missing,
stale, not-run, host-fail and incomparable semantics, not invented physical data.
Development uses existing public cases; tuning is empty. Distinct acceptance
failure families were frozen before testing and are now exposed regressions.
They are not independent operator evidence or a protected RSI holdout.
