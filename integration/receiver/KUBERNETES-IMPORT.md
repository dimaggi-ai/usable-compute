# Offline synthetic Kubernetes observations

`dimaggi_receiver.kubernetes_import` maps an explicit synthetic `batch/v1` Job
and its `v1` Pods into one workload event in the existing application observation
store. It prepares Ronnie's read path for the proposed CPU-only lab. It includes
no Kubernetes client, kubeconfig reader, credentials, network request, scheduler
mutation or retry. It does not establish a usable lab or an operator result.

This increment accepts only `evidence_class: synthetic`, including the registered
application intent. It uses Kubernetes-shaped fixtures; it does not claim they
were collected from a Kubernetes server. Live collection, source authentication,
server-version compatibility and actual executor integration remain separate
work. The adapter performs no memory, CPU, accelerator, storage or I/O sizing;
unconsumed resource declarations remain uninterpreted source data.

## Source contract and configuration

The caller registers the application intent and all three read-source roles.
The workload source must equal `expected_collector_id`. Permission and attempt
sources remain independent. The import function is:

```python
import_kubernetes(
    store,
    bundle_json,
    request_id=request_id,
    expected_collector_id=collector_id,
    expected_identity=identity,
    recorded_at_utc=collection_time,
)
```

`kubernetes_event` validates and converts without persisting. It takes the same
bundle and expected source/identity arguments plus an already validated `intent`.
The expected identity is separate caller configuration, with exactly five
nonempty string fields:

| Field | Meaning |
|---|---|
| `cluster_id` | Configured cluster identity; this adapter invents no universal Kubernetes cluster UID |
| `namespace_name` | Scoped namespace name |
| `namespace_uid` | Immutable namespace instance identity asserted by the configured source |
| `job_name` | Job name within that namespace |
| `job_uid` | Previously bound immutable Job instance identity |

These values are explicit synthetic identities in the tests. A real Job UID
cannot be guessed from its name. This adapter cannot establish a previously
unknown real UID, authenticate the namespace assertion, or reconcile an uncertain
real submission. Changing import configuration cannot replace the physical scope
or Job UID already retained for the same application request.

The JSON bundle requires exactly these outer fields:

| Fields | Type and rule |
|---|---|
| `schema` | `dimaggi-kubernetes-observation/v1` |
| `evidence_class` | `synthetic` |
| `collector_id`, `source_epoch` | Nonempty source strings; collector matches configured workload source |
| `source_sequence` | Explicit positive integer through `2**63 - 1`, allocated by the collector |
| `observed_at_utc` | UTC timestamp with `Z`, at most microsecond precision |
| `request_id`, `report_id`, `profile_id`, `target_id`, `workload_id` | Exact registered application bindings |
| `identity` | The five fields above, equal to separate expected configuration |
| `job` | A `batch/v1` Job object, or `null` for missing Job evidence |
| `pods` | Array of zero to 1,000 `v1` Pod objects; every supplied Pod must have the expected Job controller |
| `pods_complete` | Explicit boolean assertion that this collection includes the relevant Pods |
| `absence` | `null`, or the explicit not-found assertion below |

`absence` has exactly `kind: job_get_not_found`, integer `http_status: 404` and
`observed_at_utc` equal to the bundle's observation instant. Its scope is the
bundle's bound cluster, namespace and Job identity. A Job object and absence
assertion cannot coexist. Missing Job JSON or an empty Pod array alone means
missing evidence. The not-found assertion may coexist with remaining owned
Pods: it says nothing about their absence or whether effects occurred.

The outer bundle, identity and absence schemas are exact. Raw Kubernetes objects
retain additional finite JSON fields without interpreting them. Consumed fields
have strict types: booleans do not substitute for integers, condition statuses
are the strings `True`, `False` or `Unknown`, and resource versions are strings.
Transport rejects duplicate keys, invalid Unicode, non-finite numbers, excessive
nesting and input above the receiver's existing 4 MiB limit.

## Identity, ordering and preservation

Job and Pod metadata require `name`, `namespace`, `uid` and `resourceVersion`.
The Job's name, namespace and UID must match configuration. Each Pod requires
exactly one controller owner reference with `apiVersion: batch/v1`, `kind: Job`,
the expected name and UID. Labels alone are insufficient. Kubernetes namespaced
owner relationships require matching namespace and owner UID.
[Kubernetes owner references](https://kubernetes.io/docs/concepts/overview/working-with-objects/owners-dependents/)

All Pod identities, reported phases, container status records and restart counts
remain in the event payload. A later snapshot does not erase a failed Pod that
was visible earlier. Pods are retained as distinct attempts within the workload
evidence; the application does not manufacture executor attempt records for
them. Pod name reuse under a different UID remains distinguishable. A UID cannot
change its name, and a Job and Pod cannot share a UID.

`source_sequence` belongs to the collector protocol. `source_version` is the
digest of the whole bundle, so a changed Pod observation is retained even when
the Job's resource version is unchanged. Job and Pod resource versions survive
unchanged in the raw objects. This adapter compares them only for equality
within the same object UID. It intentionally does not order them or compare Job
and Pod versions: the eventual server and applicable ordering semantics are not
bound. Current Kubernetes resource-version guidance is version- and scope-aware;
this is an explicit adapter limitation, not a claim that all ordering is forbidden.
[Kubernetes API resource versions](https://kubernetes.io/docs/reference/using-api/api-concepts/#resource-versions)

Identical object UID and resource version with different contents is a durable
source conflict. Within one collector epoch, later collector sequence numbers
cannot remove a true Job `Complete`, `Failed` or `FailureTarget` condition, or
regress the phase of the same terminal Pod. Conflicting evidence remains in the
store's existing conflict journal and causes escalation; the earlier accepted
event is preserved. Late lower-sequence evidence can be appended without winning
the latest projection. A new source epoch remains an explicit source reset.
The Job API describes its terminal-condition restrictions.
[Job status reference](https://kubernetes.io/docs/reference/kubernetes-api/batch/job-v1/#JobStatus)

## Interpretation rules

The bounded adapter requires explicit Job `spec.parallelism: 1` and
`spec.completions: 1`. It preserves other specifications without treating them as
resource feasibility or authorization evidence.

| Evidence | Application workload state |
|---|---|
| Terminal Job `Complete`, complete Pod collection, an attributed successful Pod and consistent quiescent evidence | `succeeded` |
| Terminal Job `Failed` and consistent quiescent evidence | `failed` |
| Attributed running or pending Pod, without a terminal or uncertain condition | `running` or `pending` |
| A failed or successful Pod without a terminal Job condition | `unknown`; controller outcome remains unresolved |
| `SuccessCriteriaMet` or `FailureTarget` without terminal condition | `unknown`; termination is not established |
| Suspended Job | `pending` |
| Present Job with deletion timestamp, or terminating Pod evidence | `unknown`; deletion in progress is not completed deletion |
| Missing Job, incomplete Pod collection, or unsupported condition | `unknown` |
| Explicit scoped Job not-found assertion | `absent`; neither no effects nor no remaining Pods |
| Foreign UID, wrong controller/namespace, malformed consumed fields | Refuse import |

Job `Complete` and `Failed` are terminal conditions. A failed Pod can be followed
by a replacement, and a container can restart within a Pod; these are separate
from final Job outcome. Early success/failure conditions do not establish
completed termination.
[Kubernetes Job lifecycle](https://kubernetes.io/docs/concepts/workloads/controllers/job/#terminal-job-conditions)

Success requires additional attributed Pod evidence in this adapter. A completed
Job whose Pods are unavailable can therefore produce `unknown`. That is a
conservative evidence requirement for this slice, not a redefinition of the
Kubernetes Job condition. Active, ready or terminating counters, visible
nonterminal Pods, and contradictory current regular-container states prevent a
positive completion projection. A Pod's phase is a limited summary; raw container
details remain available for review.
[Pod phase and container state](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-phase)

Incompatible success and failure condition combinations produce `unknown` with
an explicit contradiction reason. The source success-policy design excludes
`SuccessCriteriaMet` combined with `Failed` or `FailureTarget`.
[Kubernetes success-policy design](https://github.com/kubernetes/enhancements/blob/master/keps/sig-apps/3998-job-success-completion-policy/README.md)

No state grants permission. The application still independently projects
permission and executor attempts: a workload absence observation cannot clear an
unknown attempt, and a workload failure does not undo an acknowledged submission.
Every result keeps execution proof false and dispatch disabled.

## Persistence and verification

Validation precedes the single append. Duplicate bundles are idempotent and
retain their first collection time. Source observation times are not refreshed
on import; existing explicit freshness limits determine when they become stale.
The observation store's [single-writer and filesystem limits](OBSERVATIONS.md)
apply. Expected IDs, source versions, hashes and JSON validation authenticate no
source and cannot detect a consistently forged synthetic history.

Run the source tests from the research repository:

```sh
PYTHONPATH=integration/receiver/src python -m pytest -q integration/receiver/tests/test_kubernetes_import.py integration/receiver/tests/test_kubernetes_adversarial.py -k 'not installed_cli'
```

The separate installed CLI test deliberately uses the test environment's actual
`dimaggi-receiver` executable with `PYTHONPATH` removed. Run the complete test
files after installing the updated wheel into that environment; an older wheel
does not establish this test. The subprocess initializes a declared local
observation journal, imports and reimports synthetic evidence, and reads the
projection. It performs no Kubernetes operation.

Independent negative cases cover owner/UID and namespace attribution, retries,
early and contradictory conditions, incomplete evidence, deletion, opaque
resource versions and terminal history. Three independently reproduced gaps in
the first draft—two incompatible condition pairs and a nonzero ready counter
under a terminal condition—were corrected before final QA. This adapter remains
synthetic product work and adds no RSI execution or usefulness-baseline credit.
