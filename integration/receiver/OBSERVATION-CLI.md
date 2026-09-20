# Local observation commands

Receiver 0.1.2 exposes the existing observation store through installed commands.
These commands read supplied files and write a local application journal. They
do not collect from Kubernetes, authenticate sources, approve actions or submit
workloads. The journal and Kubernetes importers accept synthetic evidence only.
Their output cannot establish CPU executor or operator performance.

## Configuration before import

Declare the intent using the exact fields in [OBSERVATIONS.md](OBSERVATIONS.md):
`request_id`, `report_id`, `profile_id`, `target_id`, `workload_id`, `desired_state`,
`sources` and `evidence_class`. `sources` must identify permission, attempt and
workload sources separately. This declaration comes from the intended local
experiment; importing a file cannot register its own authority or change the
intent. Source names and actor names remain attribution, not authentication.

For the synthetic TENWA export, the declared permission and attempt source IDs
are the expected journal ID followed by `/permission` and `/attempt` respectively.
For a Kubernetes fixture, the workload source ID is its separately declared
collector ID. Physical identity is another explicit file containing
`cluster_id`, `namespace_name`, `namespace_uid`, `job_name` and `job_uid`.
These are test identities; no real cluster identity is inferred.

Start a new journal with the installed executable:

```sh
dimaggi-receiver observations-init --journal /tmp/application.sqlite --intent /tmp/intent.json
```

Initialization validates the complete configuration before creating a new file
with owner-only permissions. It refuses an existing path. Every other command
requires an existing supported journal and checks its exact schema; empty,
unrelated or unsupported SQLite files are refused without initialization or
migration. The existing Darwin OFD locking limitation applies to these commands.
Keep a canonical path and close each store before opening the next one.

Existing-only commands also refuse SQLite `-journal`, `-wal` or `-shm` sidecars
before opening SQLite. A crashed writer can leave a hot rollback journal that
SQLite would otherwise recover before schema validation, modifying a file the
command subsequently refuses. Preserve the database and all sidecars for an
explicit recovery review of the owned file; the CLI performs no automatic repair.
This conservative rule also refuses leftover sidecars that might be harmless.
It assumes the documented cooperative single-writer/canonical-path discipline,
not protection from an unrelated process changing files concurrently.

## Import and inspect

With the explicit request and expected journal IDs from that declaration:

```sh
dimaggi-receiver journal-import --journal /tmp/application.sqlite \
  --request-id synthetic-request-0 --input /tmp/export.json \
  --expected-journal-id EXPECTED_JOURNAL_ID --recorded-at 2026-09-19T12:00:01Z
dimaggi-receiver kubernetes-import --journal /tmp/application.sqlite \
  --request-id synthetic-request-0 --input /tmp/kubernetes.json \
  --expected-collector-id EXPECTED_COLLECTOR_ID --expected-identity /tmp/identity.json \
  --recorded-at 2026-09-19T12:00:02Z
dimaggi-receiver observations-show --journal /tmp/application.sqlite \
  --request-id synthetic-request-0 --as-of 2026-09-19T12:00:03Z \
  --freshness /tmp/freshness.json
dimaggi-receiver observations-history --journal /tmp/application.sqlite \
  --request-id synthetic-request-0
```

`EXPECTED_JOURNAL_ID` and `EXPECTED_COLLECTOR_ID` are placeholders for the
separately declared test sources. `/tmp/freshness.json` must supply explicit
positive finite seconds for `permission`, `attempt` and `workload`; there is no
default TTL. Dates are explicit test clocks, not trusted authorization time.
Imports preserve source timestamps. Repeating the same source evidence does not
refresh it. See [journal import](JOURNAL-IMPORT.md) and
[Kubernetes import](KUBERNETES-IMPORT.md) for their distinct checks and limits.

`observations-show` computes the current projection without persisting a
reconciliation snapshot. `observations-history` returns source events.
`observations-reconcile` takes the same arguments as `observations-show` and
persists an immutable application reconciliation. A source conflict may be
recorded by an importer even when that import refuses; previous accepted events
remain intact. An import spanning several events is not a single transaction.

## Review notes

`observations-triage` requires `--triage-id`, `--reconciliation-id`, `--case-id`,
`--actor-id`, `--disposition` (`hold`, `escalate` or `proposal`), `--reason`,
`--recorded-at` and `--journal`.

`observations-resolve` requires `--resolution-id`,
`--opening-reconciliation-id`, `--resolving-reconciliation-id`, `--case-id`,
`--actor-id`, `--reason`, `--recorded-at` and `--journal`. It applies the existing
bounded resolution allowlist and rechecks current evidence. It records only
that an application evidence condition has cleared. It cannot resolve uncertain
external effects, override source conflicts or grant permission.

Commands expose `--help`. Invalid content or refused operations return structured
JSON with exit code 2; command-line syntax errors use argparse's usage message.
Notes never request mutations. These commands and fixtures are product work,
not RSI execution or evidence of digest value.
