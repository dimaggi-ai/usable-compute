# Records admission

`rsi_records.assess` requires supplied artifact bytes and an owner-provisioned
`rsi_admission.ReplayStore` before records v1 can receive useful credit. Each
artifact must pass admission and match its task's repository, snapshot, profile,
objective, denominator, unit and workload. Evidence references alone produce
`insufficient_evidence` with zero useful credit. The command-line records reader
has no artifact input and therefore cannot award credit.

Provision the replay registry once with `ReplayStore(path, create=True)`; reopen
it with `ReplayStore(path)`. Consumption atomically records artifact, payload, row
and source identities. Reusing them in another call, including after restart,
is refused. Records consume all admitted batches in one transaction when their
recorded criteria are met. `_assess_records` is an internal consistency calculator
used by the chronology regression suite; consumers must use `assess`.

The owner must authenticate and freeze the trust bundle independently, and protect
the replay registry from replacement, deletion and rollback. Hashes establish
consistency, not provenance. Only the synthetic resource-count profile is admitted;
no result authorizes execution, continuation or genuine-data acceptance.
