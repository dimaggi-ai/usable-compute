# Infrastructure operations with attributable evidence

Receiver 0.1.8 connects provider telemetry, workload planning and source review.
A compatible assessment means the declared checks passed. It does not reserve
capacity, grant permission or establish physical performance. TENWA remains the
separate signed, bounded CPU execution boundary.

## What this adds

| Capability | Integrated behavior | Evidence boundary |
|---|---|---|
| NVIDIA memory and power | Fixed-query SMI CSV becomes exact byte/milliwatt observations with source, epoch, target and time | Replay tested; no physical GPU run. Unsupported values remain unknown. MIG requires a separate supported mapping. |
| TPU memory | Raw Monitoring byte gauges are paired per accelerator ID and exact timestamp, including nanoseconds | Runtime device IDs are not physical-chip counts. Explicit one-to-one inventory is required; shared HBM mappings refuse. |
| Admission | `infrastructure-assess` recomputes the plan, checks pinned telemetry against its selected pools, enforces device memory, driver and inventory constraints, and blocks unresolved source reviews | Stack, topology, health, reservations and workload SLO measurements remain separate trusted inputs. Unassessed pools are named. |
| Unknown measurements | Required unknown headroom or SLOs refuse. Explicitly unrequested constraints are listed in each allocation | A CPU smoke test makes no network-bandwidth, power, latency or restore claim. |
| Source monitoring | Official-source bytes, hashes, impact mappings and persistent review findings survive collection failure, recovery and source reversion | No automatic profile activation; even cosmetic byte changes require review. |
| Failure handling | Real local transport loss/crash/timeout preserves uncertainty and prevents a second submission | Local Kubernetes evidence, not accelerator validation or independent acceptance. |

SMI's output compatibility is narrower than NVML's API compatibility. The adapter
supports the documented fixed query below and rejects unexpected formats; it
makes no claim to support every future driver. See [NVIDIA SMI documentation](https://docs.nvidia.com/deploy/nvidia-smi/).
TPU fields follow [Google's monitoring metrics](https://docs.cloud.google.com/tpu/docs/monitor-tpu)
and the [Monitoring list API](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.timeSeries/list).

## Reproduce both provider paths offline

Install the wheel and run from the repository root. The committed examples are
**synthetic**, including their profile validation and source-review baseline.
They cannot activate a real executor.

```sh
python integration/receiver/examples/operations/generate.py
python - <<'PY'
import json, subprocess
from pathlib import Path
for provider in ('nvidia', 'google'):
    root = Path('integration/receiver/examples/operations') / provider
    pins = json.loads((root / 'pins.json').read_text())
    subprocess.run([
        'dimaggi-receiver', 'infrastructure-assess',
        '--registry', str(root / 'registry.json'),
        '--registry-digest', pins['registry_digest'],
        '--input', str(root / 'request.json'), '--as-of', pins['as_of'],
        '--evidence', str(root / 'evidence.json'),
        '--evidence-digest', pins['evidence_digest'],
    ], check=True)
PY
```

The CLI uses exit 2 for malformed/untrusted input. A well-formed assessment with
`status: refused` is a successful evaluation and exits 0. Automation must inspect
`status`, not infer compatibility from process exit alone.

## Explicit read-only collectors

Metadata has exactly `source_id`, `source_epoch`, `target_id`, `observed_at`, and
`evidence_class`. Live commands require `hardware_observed` and set collection
start time themselves. Labels express the configured source class; they do not
certify that an untrusted supplied executable represents actual hardware.

For a reviewed NVIDIA host, pin the absolute `nvidia-smi` executable by SHA-256:

```sh
dimaggi-receiver telemetry-collect-nvidia \
  --binary /reviewed/path/nvidia-smi --binary-digest sha256:REVIEWED_DIGEST \
  --metadata /private/telemetry-source.json --freshness 300
```

Only this read-only query runs, with a 15-second deadline and 4 MiB output budget:

```text
--query-gpu=uuid,name,driver_version,memory.total,memory.used,memory.free,power.draw,power.limit,mig.mode.current
--format=csv,noheader,nounits
```

For TPU telemetry, `identity.json` contains exactly `project_id`, `zone`, and
`instance_id`. Supply an owner-only regular token file; no ambient credential,
kubeconfig, proxy or authentication discovery is used. Darwin ACLs are checked.
Use a separately authorized Monitoring read credential, not a private signing key.

```sh
dimaggi-receiver telemetry-collect-google \
  --identity /private/identity.json --token-file /private/monitoring.token \
  --metadata /private/telemetry-source.json \
  --start-time YYYY-MM-DDTHH:MM:SSZ --freshness 600
```

Only `memory_total` and `memory_used` GAUGE/INT64 byte series are requested, without
aggregation. The query window is at most one hour; pagination, total time and
bytes are bounded. Partial results, unreachable regions, page cycles, foreign
resources and unpaired samples refuse. The snapshot digest binds the combined
complete raw series; it excludes bearer and pagination tokens. Choose freshness
against actual metric publication delay; a delayed gauge is not instantaneous
health or a scheduler reservation.

## Source watch and explicit review

```sh
python integration/receiver/tools/source_watch_run.py \
  --config integration/receiver/config/source-watch.json \
  --history /approved/evidence/source-history
```

The six configured URLs cover TPU releases, runtime and telemetry, NVIDIA SMI,
GPU Operator and Network Operator. Each GET is limited to 15 seconds and its
configured byte budget. Redirects, ambient proxies, empty/oversized responses
and unsupported formats refuse. A subprocess deadline also bounds DNS. Failed
collection produces an incomplete report and a persistent review finding.

Each run retains raw sources, a snapshot, a comparison report and a hashed queue.
An exclusive lock prevents concurrent writers. A missing queue from an interrupted
run stops subsequent processing. Retention stops at 90 snapshots or a conservative
256 MiB budget; archive history and preserve unresolved reviews before resuming.
Schedule this single-run command with the host's service manager. The deployed
engineering instance runs daily on the user's Mac and writes only local evidence.

Do not delete a queue entry because a source recovers or reverts. After inspecting
the exact before/after bytes, a trusted history owner can record a scoped review:

```sh
python integration/receiver/tools/source_watch_review.py \
  --history /approved/evidence/source-history \
  --queue-digest sha256:EXACT_CURRENT_QUEUE_DIGEST \
  --finding-id sha256:EXACT_FINDING_ID \
  --evidence /approved/evidence/review.txt --reviewer REVIEWER_ID
```

This records only a `no_material_change` disposition, retains the review evidence,
and preserves the source's original observation time. It does not validate or
activate a profile. Material changes require a new candidate profile and actual
compatibility validation; their findings stay unresolved. The admission bridge
requires a queue less than 24 hours old and an explicit watch mapping for each
selected accelerator profile. Source hashes identify bytes, not truth.

## Local transport fault campaign

`tools/local_transport_faults.py --help` describes an opt-in real campaign using
an existing, approved loopback kind lab, fresh infrastructure registry/request,
TENWA runner/adapter, explicit kubeconfig and a new private output directory.
It injects lost response, process death before/after upstream creation, and timeout
through an ephemeral local TLS proxy. It validates exact Job bytes and checks that
one transport entry remains uncertain and resubmission is refused.

The two forwarded Jobs must complete with exact output. The other two must be
absent at observation. Cleanup uses Kubernetes UID preconditions, and all campaign
keys are revoked. A failed or interrupted run retains evidence; inspect the ledger
and cluster before resuming. This harness is not a production fault injector.

## Verification and boundaries

Run `tools/verify_interoperability.py` from a freshly installed matching wheel
with reviewed TENWA and source-export paths. It checks installed/source hashes,
Go race/vet, the full receiver suite with no skipped acceptance tests, native
contract interoperability and a bounded synthetic planner benchmark.

The engineering record links these results in [the autonomous delivery report](AUTONOMOUS-DELIVERY.md).
Ronnie/Dmitry's acceptance remains independent. Physical TPU/GPU stack validation,
distributed performance and longitudinal RSI observations still require their
respective hardware, people or elapsed time. Keep both PRs draft and unmerged.
