# One-shot Mac lab collection

`collect_lab.py` invokes the existing read-only TLS collector once and appends its
observation to a persistent Darwin journal. It does not submit a Job, read
kubeconfig, choose a target, mint a grant or relax Pod matching. The integrating
operator supplies all identities and trust material. Follow the
[connection runbook](../KUBERNETES-LAB-CONNECTION.md) for a remote lab; a local lab
can use its approved explicit HTTPS endpoint directly.

Run from the research repository root with the receiver dependencies installed:

```sh
PYTHONPATH=integration/receiver/src python3 integration/receiver/tools/collect_lab.py \
  --config /approved/private/lab-collector.json \
  --ca /approved/private/cluster-ca.pem \
  --token-file /approved/private/read-token \
  --registration /approved/private/observed-intent.json \
  --intent /approved/private/exact-planning-intent.json \
  --report /approved/private/exact-report.json \
  --journal /approved/private/observations.sqlite
```

All paths are placeholders. Keep the token and journal outside the repository in
an owner-controlled directory. The token file must be owned by the invoking user, have no group/other mode
permissions and no stored extended ACL, and must not be a symlink. Checks apply
to the opened descriptor before reading. Keep its parent directory trusted;
these checks do not audit ancestor access or prevent subsequent owner changes.
The token is read from a bounded regular file;
one trailing LF is removed. It is never supplied in the command line, printed,
or copied into the journal by this harness. The remote content and journal remain
sensitive. Do not publish the private configuration or raw journal wholesale.

`--config` is a strict JSON object containing the `CollectorConfig` field names
except `ca_pem` and `bearer_token`, which must be supplied through their separate
files. Required fields are `endpoint`, `collector_id`, `source_epoch`,
`cluster_id`, `target_id`, `namespace_name`, `namespace_uid`, `job_name`, `job_uid`,
`verifier_path`, `verifier_digest` and `valid_until_utc`. Optional bounds and the
version-pinned `profile` and opt-in `pod_profile` retain the collector's documented defaults. Unknown
configuration fields refuse. Use an absolute path to a trusted native
`batch-object-check` binary built from the independently approved TENWA revision and
supply its independently retained `sha256:` digest. The collector verifies the
binary before invoking it; this script never derives a replacement expected
digest from whichever binary happens to be present.

`--registration` is the exact application observation intent: `request_id`,
`report_id`, `profile_id`, `target_id`, `workload_id`, `desired_state`, `sources`
and `evidence_class`. `evidence_class` must explicitly be `observed`; the script
does not relabel synthetic intent. The `sources` object declares the `permission`,
`attempt` and `workload` source IDs. Registering them does not create evidence for
those roles. The workload source must equal `collector_id` and workload ID must
equal the configured Job name. See the [journal contract](../OBSERVATIONS.md).
Planning intent and report are passed as their original bytes; the collector
checks their registration and physical-scope bindings.

The first invocation registers the declared sources and intent, then collects.
Repeating the same inputs reopens the same journal and continues its sequence.
Registration can remain recorded if a later collection refuses; no successful
workload observation is implied by registration alone. Existing conflicting
registrations refuse instead of being overwritten. `:memory:` and non-Darwin
persistence refuse before network access.

Exit 0 means collection completed and the receipt was emitted, **not** that the
workload succeeded. Read `result.state`, `pod_spec_verified` and `output_verified`.
Unsupported admitted Pod fields remain `unknown`. To use the narrow verified
v1.35.0 stock admission rules, explicitly set `pod_profile` to
`kubernetes-pod-admission/v1.35.0-stock/v1` and use a new source epoch if previous
observations used the strict default. The receipt includes exact
planning/verifier digests, collection timestamps and sequence, but excludes raw
API objects and logs. `execution_proven` remains false and permission remains
not granted. Exit 2 emits a generic refusal without underlying error details;
inspect the private configuration and trusted journal locally to diagnose it.

Local tests use synthetic resources served over local TLS, including a verifier
test double. They exercise persistence across reopen, unknown-result retention,
credential-output exclusion and refusal before HTTP for invalid inputs. They
are not real-cluster or pinned-native-verifier proof:

```sh
PYTHONPATH=integration/receiver/src python3 -m pytest integration/receiver/tests/test_collect_lab_harness.py -q
```

Editorial review: passed — input contracts, digest provenance, persistence, exit semantics
and evidence limits checked against the script and underlying collector. No live
collection is claimed by this guide.

The Darwin ACL check follows [Apple libc implementation](https://github.com/apple-oss-distributions/Libc/blob/main/posix1e/acl_file.c); absent ACL metadata is distinguished from retrieval failures.
