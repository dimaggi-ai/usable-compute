# Read-only RSI record consistency

`rsi_records.py` checks caller-supplied baseline, weekly and disposition records
for the approved `scheduler-vs-more-gpus` and `span-contract` scope. It does not
run either repository, fetch evidence, authenticate people, verify the truth of
a report, approve continuation or execute a candidate. A declared `observed`
label is not proof that events occurred. Use the [read-only digest](READ_ONLY_DIGEST.md)
to collect its separate snapshot preview; neither tool supplies human acceptance.

From the research root:

```sh
python3 tools/rsi_records.py --input /path/to/actual-records.json
python3 -m unittest discover -s tools -p test_rsi_records.py -v
```

Exit 0 means the record has a valid supported shape and an assessment was emitted,
including an insufficient or failed assessment. Invalid shape, scope or values
return exit 2 with an error on stderr. Always read the assessment fields.
JSON is bounded to 256 KiB, 16 nested levels, 256 entries per array and 2,048
characters per string. Duplicate decoded keys, nonfinite numbers, invalid Unicode,
unknown fields and numeric booleans refuse. The validator performs no evidence
path reads; references are recorded claims, not verified artifacts.

## Exact record shape

The schema is `dimaggi-rsi-records/v1`. All fields listed here are mandatory;
unknown numeric measurements and evidence references use null. Empty arrays
mean no records collected. This blank record deliberately produces insufficient
evidence, not a successful baseline or four fabricated weeks:

```json
{
  "schema": "dimaggi-rsi-records/v1",
  "evidence_class": "observed",
  "repositories": ["scheduler-vs-more-gpus", "span-contract"],
  "required_checks": [],
  "tasks": [],
  "weeks": [],
  "dispositions": [],
  "setup": {"active_seconds": null, "cost_amount": null, "currency": null, "evidence": null}
}
```

`evidence_class` is `observed` or `synthetic`; tests use explicitly synthetic
fixtures. Neither choice grants permission. The expected required-check list
must come from the actual controlled acceptance plan; the tool cannot establish
that a caller supplied the complete or correct list.

| Object | Exact fields and meaning |
|---|---|
| Task | `id`, `manual_binding`, `assisted_binding`, `manual`, `assisted`, `checks`, `exposure`, `evidence`. IDs are unique. Exposure records ordering, prior familiarity and remaining comparison limits; null is insufficient. |
| Each binding | `repo`, `snapshot`, `profile`, `objective`, `denominator`, `unit`, `workload`, all nonempty strings. Manual and assisted bindings must match exactly; mismatch records incomparability and fails the recorded gate. |
| Each effort record, including setup | `active_seconds`, `cost_amount`, `currency`, `evidence`. Time/cost is finite and nonnegative, or null; booleans refuse. Currency is three uppercase ASCII letters or null. Mixed currencies refuse because conversion is outside this contract. |
| Each task check | `id`, `outcome`, `evidence`. ID belongs to `required_checks` and occurs once per task. Outcome is `pass`, `fail`, `missing` or `not_run`. Every required check is needed for each task; omission/unrun/missing evidence remains insufficient. |
| Each week | `id`, `start`, `end`, `recorded_at`, `task_ids`, `evidence`. Timestamps use explicit UTC `Z`. This record format uses seven-elapsed-day windows, ordered and nonoverlapping, ending no later than the evaluation clock. The recording timestamp is at or after the completed window and no later than the evaluation clock. A task cannot count in two weeks. |
| Each disposition | `id`, `finding_id`, `task_id`, `kind`, `prior_known`, `owner`, `accepted_at`, `evidence`. IDs and finding IDs are unique; task must exist. Kind is `useful_change`, `justified_no_change`, `rejected` or `incomplete`; prior-known is boolean. Owner, acceptance time and evidence may be null but then cannot supply useful credit. Acceptance time cannot be in the future. |

Include ongoing reviewer maintenance in the corresponding assisted task's
`active_seconds` and cost, allocated once; keep setup in the separate object.
Retain raw time/cost records and the baseline-freeze/order evidence externally.
The tool checks the declared comparability and exposure fields, not whether
human work was timed correctly or baseline choices were made prospectively.
Unknown is not zero. Any incomplete record suppresses aggregate totals to null
rather than presenting partial sums as full measurements.

## Preliminary assessment

The inherited gate is four actual weekly observations, at least two unique,
evidence-linked useful dispositions on comparable tasks, no failed mandatory
controlled check, and no increase in comparable ongoing review effort. Setup
is reported separately and included in assisted total effort/cost. The record
also requires matched tasks for both approved repositories and evidence for
time/cost. The seven-day encoding is this tool's weekly record convention, not
a new performance or usefulness target.

Missing information yields `insufficient_evidence`; complete records that fail
comparison/check/usefulness/effort conditions yield `not_met`; otherwise the
limited result is `recorded_criteria_met`. Synthetic input always has the top-level
assessment `synthetic_only`, even if its test data satisfies the recorded gate.
Prior-known findings receive no new usefulness credit. This version does not
award prevention credit or infer independence from different IDs or owner names.

Every result sets `authenticated`, `human_reports_verified`,
`continuation_authorized` and `candidate_execution_authorized` to false. An owner
must inspect actual evidence and independence before any continuation decision.
Four immediately generated windows or forged references can still be false
claims; date consistency cannot turn them into observed facts. This tool creates
no observations, baseline, schedule, authority, broader repository scope or
protected evaluator.

The synthetic suite covers changed denominators and identities, duplicate
finding/task credit, missing checks/time/cost/owners, failed mandatory checks,
increased effort, prior-known claims, invalid numeric/JSON/Unicode input,
scope drift, future/overlapping/nonweekly dates and optimized-interpreter
refusal. No passing test is credited as a real useful disposition or week.

Editorial review: passed — schema fields, inherited gate, units, commands,
unknown handling, synthetic labels and proof limits checked against code/tests.
