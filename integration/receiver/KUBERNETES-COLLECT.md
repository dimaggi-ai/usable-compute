# Explicit TLS Kubernetes collection

`dimaggi_receiver.kubernetes_collect` reads one configured Kubernetes target and
appends attributed workload evidence to the application observation journal. It
is a library for a trusted integrating host, not a generic JSON import or a
scheduler. Its remote operations are GET requests. It cannot create, modify,
retry or delete workloads, install isolation policies, or grant execution
permission.

## Configuration and source identity

The host supplies a `CollectorConfig` containing an explicit HTTPS API origin,
CA certificate PEM, in-memory bearer credential, collector ID and source epoch,
cluster and application target IDs, dedicated namespace name/UID, and an already
trusted Job name/UID. It also supplies an absolute verifier-binary path, its exact
SHA-256 digest, the supported comparison profile, finite read/collection bounds,
and a configuration expiry. The application must already contain the matching
observed intent and registered workload source.

The CA verifies the configured TLS server identity; it is not a grant-signature
verification key. No kubeconfig, ambient trust pool, proxy setting, credential
plugin or discovery response chooses the target. The supported Job comparison
profile is `kubernetes-job-response/v1.35.0-cpu/v1`. Source configuration must
establish its applicability to the actual deployment; a response cannot select a
more permissive profile.

The verifier and its containing directory must remain trusted and stable during
hashing and execution. A matching hash does not secure an attacker-writable path.
The collector verifies the binary digest and checks its returned object/report
byte commitments, pinned UID, current response resourceVersion and inert
permission fields. The Job UID comes from trusted configuration. ResourceVersion
comes from the TLS-acquired response and binds that exact comparison; it is not
an independently authenticated clock or an ordered freshness measure.

The collector sets `source_authenticated: true` only for evidence it acquired
through the configured TLS origin inside the trusted host process. This is
source attribution at that boundary, not physical workload attestation or proof
that the cluster's reports are true. Arbitrary JSON claiming `observed` cannot
enter through the separate synthetic Kubernetes import path.

## Read sequence and interpretation

A normal collection performs five base GETs: namespace, named Job, a bounded Pod
list selected by the pinned Job UID, the same Job again, and the namespace again.
The list must declare no continuation and no remaining items; more than 1,000
Pods refuses. The collector does not follow pagination automatically. Both
namespace reads must match the pinned identity and show no deletion timestamp.
The two Job representations must agree. These checks do not make the reads an
atomic snapshot or close the namespace replacement race for a future submitter.

The separately pinned verifier checks the Job against the exact planning bytes.
The collector then compares each supported Pod spec with the verified Job's
stored template. Its narrow comparison permits a syntactically valid assigned
`nodeName` and omission of the three false host-namespace booleans. Other changes,
including unsupported admission defaults or injected fields, do not receive a
spec-match claim.

For each eligible successful terminal Pod, the collector makes three additional
GETs: the named Pod, its single container's non-following log, then that Pod again.
The Pod must remain unchanged around the log read. The path fixes the container,
disables follow and timestamps, and checks the bounded returned bytes against the
verifier's expected stdout digest. Eligibility includes the supported one-container
shape, zero restart count and zero exit status. It does not read arbitrary log
paths or stream indefinitely.

A retained `succeeded` interpretation requires the existing Job/Pod status checks
plus the collector's supported Pod-spec and output checks. A success claim that
lacks those runtime checks becomes `unknown`. Other valid failure or absence
interpretations retain their own meanings. A Job GET returning 404 is explicit
absence evidence only; it is never success and never permission to resubmit.
No result establishes execution permission, settles an ambiguous prior dispatch,
or substitutes for independent operator acceptance. The local TLS tests use
synthetic resources and output, even when they exercise this observed-source
acquisition path.

## Bounds, deadlines and credentials

Response limits are explicit, positive and at most 1 MiB. Log reads additionally
apply the verifier's positive output bound, at most 64 KiB. Reads use a limit plus
one refusal byte; oversize or encoded responses refuse. Combined application
events retain the receiver's 4 MiB bound. JSON checks reject malformed Unicode,
duplicate keys, unsupported structure and nonfinite numbers.

The HTTP parser applies its own bounded header parsing before the collector
checks that accepted header-name/value lengths total at most 32 KiB. This is an
accepted-header limit, not a claim that only 32 KiB can enter the parser. Redirects
and non-200 responses refuse, except for the explicitly interpreted Job 404.
There are no automatic retries or redirected credential forwarding.

Both configured time bounds are positive and at most 30 seconds, and the socket
bound cannot exceed the collection bound. Once connected, a watchdog retains the
actual socket and shuts it down at the collection deadline. It interrupts
trickled headers and bodies even when `http.client` transfers a connection-close
socket to the response. The verifier subprocess receives the remaining time
budget, and final checks reject exceeded deadlines, clock rollback or expired
configuration.

**This is not a hard process deadline.** Platform DNS resolution is not
cancellably bounded by the socket timeout; connection setup and local I/O are not
covered by the post-connect watchdog. The host must provide the isolation and
process supervision required for a stronger deadline guarantee. The tests verify
local TLS post-connect behavior, not resolver cancellation or all platform
failure modes.

Configuration and collector representations redact credentials. Errors use local
categories rather than returning network errors containing deployment details.
A response containing literal credential bytes is refused. That check is not
general sanitization of encoded or derived secrets; treat collected raw content
and journal access as sensitive. Do not log the supplied credential or assume
raw server content is safe to publish.

## Journal continuity and use

Before materializing a request history, the collector checks its stored byte
count and event count. Collection refuses at 10,000 events or 16 MiB, and refuses
an append that would exceed the byte cap. History is never truncated or deleted;
archival/retention operations require a separate reviewed contract. This bounds
collector history memory, not the duration of arbitrary storage operations.

The collector derives the next source sequence from durable history, including
after a real store reopen. The same source epoch cannot silently change its
endpoint, CA, identity, target, profile or verifier digest. An explicit new epoch
records such a configuration change. Credential rotation alone is not a source
identity change. Conflicting content under the same immutable object version
refuses; it does not overwrite the prior observation. These are local history
checks, not multi-host fencing or an external rollback-proof service.

Call serially under the journal owner using independently obtained configuration
and a trusted UTC clock:

```python
collector = Collector(config, clock=trusted_clock)
result = collector.collect(
    store,
    request_id=request_id,
    intent_json=exact_intent_bytes,
    report_json=exact_report_bytes,
)
```

The example intentionally supplies no target or credentials. Establishing the
lab target, trusted Job UID, namespace lifecycle protection, isolation, current
configuration and authority remains the integrating deployment's responsibility.
See [observation journal semantics](OBSERVATIONS.md) and
[synthetic Kubernetes import](KUBERNETES-IMPORT.md) for the separate evidence paths.

Local verification from the repository root, with the receiver's test
dependencies installed:

```sh
PYTHONPATH=integration/receiver/src python3 -m pytest integration/receiver/tests/test_kubernetes_collect.py integration/receiver/tests/test_kubernetes_collect_adversarial.py -q
```

Editorial review: passed — configuration boundaries, source-authentication scope,
GET counts, runtime matching, refusal semantics, parser limits, deadline caveats
and journal claims checked against the implementation. No live-cluster or
operator proof is claimed.
