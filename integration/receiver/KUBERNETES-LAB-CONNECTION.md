# Mac collector connection to a Kubernetes lab

This runbook prepares a connection for the existing read-only collector. It has
not been exercised against a live lab. Provisioning, workload submission and
cleanup belong to the separately authorized lab procedure. A reachable endpoint
alone does not establish execution permission or operator acceptance.

## Supported host and prerequisites

Keep the receiver, pinned native `batch-object-check` verifier and persistent observation
journal on the supported Mac host. File journals currently require 64-bit Darwin
OFD locking and refuse other platforms before opening the journal. Linux
`:memory:` operation is nondurable and cannot replace the durable acceptance
path. The shipped release environment was tested on Darwin ARM64, Python 3.12
and Go 1.26.1; this runbook adds no Linux support claim.

Obtain the VM/project/zone, approved SSH route, Kubernetes API listener address
as seen from that VM, cluster CA PEM, serving-certificate identities and exact
Kubernetes version from the lab owner. Separately obtain the dedicated namespace
name/UID, trusted Job name/UID, target identity, scoped read credential, source
epoch, comparison profile, expiry and pinned verifier digest required by
[collector configuration](KUBERNETES-COLLECT.md). The cluster CA authenticates the
API server; public grant verification keys serve a different purpose. Do not
place credentials in command arguments, documentation or retained transcripts.

## Forward the API without changing TLS verification

The following is a parameterized example, not populated deployment configuration.
It assumes an owner-approved IAP SSH route and an API listener on VM loopback
port 6443. Confirm those assumptions before use. It opens only a local listener;
it does not create a VM, cluster or workload. `gcloud compute ssh` can manage SSH
key metadata as part of authentication. Its `--` separator passes subsequent
arguments to SSH. [Google Cloud command reference](https://docs.cloud.google.com/sdk/gcloud/reference/compute/ssh)

```sh
LAB_PROJECT='REPLACE_WITH_APPROVED_PROJECT'
LAB_ZONE='REPLACE_WITH_VM_ZONE'
LAB_VM='REPLACE_WITH_VM_NAME'
gcloud compute ssh "$LAB_VM" --project="$LAB_PROJECT" --zone="$LAB_ZONE" \
  --tunnel-through-iap -- \
  -N -L 127.0.0.1:16443:127.0.0.1:6443 \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30
```

Keep that foreground connection running during collection. Local port 16443 must
be free. The second `127.0.0.1` denotes VM loopback, not the Mac. Use the owner's
actual listener address/port if different. IAP access and SSH configuration must
already be available; this command does not establish them.

The collector endpoint can be `https://127.0.0.1:16443` **only if the API serving
certificate actually includes the IP address `127.0.0.1` in its subject alternative
names** and validates against the supplied CA. The collector uses its endpoint
hostname for TLS identity verification and has no separate server-name override.
SSH transport does not relax that check. If the certificate lacks that identity,
the owner must provide a verified certificate-matching endpoint route or
configure an appropriate lab serving certificate before collection. Do not use
an insecure TLS option or substitute the SSH host key for the cluster CA.

Invoke the existing collector library from the trusted host integration using
this explicit endpoint and its independently supplied configuration; see the
[actual library call](KUBERNETES-COLLECT.md#journal-continuity-and-use). There is no
standalone collector CLI asserted by this runbook. Keep the durable journal under
one owner. Stop the foreground tunnel with Ctrl-C after collection; that closes
the tunnel and does not clean up cloud resources or workloads.

## Preserve strict compatibility checks

The Job comparison profile is version-pinned. Its Job-template defaulting checks
do not imply acceptance of every Pod produced by Kubernetes 1.35. The current
runtime comparison permits an assigned, syntactically valid `nodeName` and
omission of three false host-namespace flags. Unexpected admission fields remain
unverified, so a successful API status alone cannot become verified workload
success.

In particular, Kubernetes v1.35.0's DefaultTolerationSeconds admission plugin can
add NoExecute tolerations for not-ready and unreachable nodes. Its default
300-second values are configurable; they are not a universal deployment
contract. [Pinned upstream plugin source](https://github.com/kubernetes/kubernetes/blob/66452049f3d692768c39c797b21b793dce80314e/plugin/pkg/admission/defaulttolerationseconds/admission.go)

Before extending compatibility, retain the actual admitted Pod and Job, verified
server version and relevant admission configuration. Compare every added or
changed field against pinned upstream behavior, add positive fixtures and
negative mutation cases, and keep unknown fields refused. No such extension or
live-cluster compatibility proof is supplied here.

The collector's post-connect watchdog bounds slow headers and bodies. Platform
DNS resolution is not cancellably bounded, and this is not a hard process
deadline; see [bounds and deadlines](KUBERNETES-COLLECT.md#bounds-deadlines-and-credentials).

Editorial review: passed — command syntax checked against the official command reference;
platform, TLS, runtime matching and deadline statements checked against the
collector source. Deployment parameters remain explicit placeholders. No cloud
command or live collection was executed during this documentation review.
