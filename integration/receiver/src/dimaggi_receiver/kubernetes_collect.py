"""Explicit TLS read-only collection into the application observation journal.

The trusted host owns configuration, clock, verifier binary and journal. No JSON
import can select this path or assert that a TLS exchange occurred.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import http.client
import math
from pathlib import Path
import re
import ssl
import socket
import threading
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, urlencode

from .jsonio import MAX_BYTES, canonical, digest, loads
from .kubernetes_import import IDENTITY_KEYS, SCHEMA, _kubernetes_event, _persist_event
from .observations import IDENTITIES, ObservationError, ObservationStore, _utc

PROFILE = "kubernetes-job-response/v1.35.0-cpu/v1"
POD_PROFILE = "kubernetes-pod-admission/v1.35.0-stock/v1"
MAX_HISTORY_BYTES = 16 << 20
MAX_HISTORY_EVENTS = 10000
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, repr=False)
class CollectorConfig:
    endpoint: str
    ca_pem: str = field(repr=False)
    bearer_token: str = field(repr=False)
    collector_id: str
    source_epoch: str
    cluster_id: str
    target_id: str
    namespace_name: str
    namespace_uid: str
    job_name: str
    job_uid: str
    verifier_path: str
    verifier_digest: str
    profile: str = PROFILE
    pod_profile: str = ""
    timeout_seconds: float = 5
    max_collection_seconds: float = 30
    response_limit: int = 1 << 20
    valid_until_utc: str = ""

    def __repr__(self):
        return "CollectorConfig(configuration redacted)"


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ObservationError("collector clock must provide aware UTC time")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


class Collector:
    """One configured read-only source. Collect serially under the journal owner.

    No kubeconfig, ambient CA/proxy, watch, redirects, retries or mutations. Bounded non-following logs are read
    only for stable attributed successful Pods.
    DNS resolution is platform-owned; socket timeouts are not a DNS hard deadline.
    """
    def __init__(self, config: CollectorConfig, *, clock=lambda: datetime.now(timezone.utc)):
        try:
            endpoint = urlsplit(config.endpoint)
            if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username is not None
                    or endpoint.password is not None or endpoint.path not in ("", "/") or endpoint.query
                    or endpoint.fragment or len(config.endpoint) > 2048 or "\\" in config.endpoint
                    or not 0 < (endpoint.port or 443) <= 65535):
                raise ValueError()
            for name in (config.namespace_name, config.job_name):
                if not _LABEL.fullmatch(name):
                    raise ValueError()
            for value in (config.namespace_uid, config.job_uid, config.collector_id, config.source_epoch, config.cluster_id):
                if not _TOKEN.fullmatch(value):
                    raise ValueError()
            if (config.profile != PROFILE or config.pod_profile not in ("", POD_PROFILE) or not _SHA.fullmatch(config.verifier_digest)
                    or not Path(config.verifier_path).is_absolute()
                    or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", config.bearer_token)
                    or len(config.bearer_token) > 8192 or not 0 < len(config.ca_pem) <= 1 << 20
                    or type(config.response_limit) is not int or not 1 <= config.response_limit <= 1 << 20):
                raise ValueError()
            for bound in (config.timeout_seconds, config.max_collection_seconds):
                if type(bound) not in (int, float) or not math.isfinite(bound) or not 0 < bound <= 30:
                    raise ValueError()
            if type(config.target_id) is not str or not 0 < len(config.target_id) <= 2048 or any(ord(c) < 32 for c in config.target_id):
                raise ValueError()
            if config.timeout_seconds > config.max_collection_seconds:
                raise ValueError()
            _utc(config.valid_until_utc)
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_verify_locations(cadata=config.ca_pem)
        except (ValueError, TypeError, AttributeError, ssl.SSLError):
            raise ObservationError("collector configuration refused") from None
        self._config, self._clock, self._tls = config, clock, tls
        self._host, self._port = endpoint.hostname, endpoint.port or 443
        self._identity = {key: getattr(config, key) for key in IDENTITY_KEYS}
        # Credential rotation does not change a source. CA/endpoint/identity/profile
        # changes do, and must be explicitly represented as a new source epoch.
        self._descriptor = {"endpoint": config.endpoint, "ca_digest": "sha256:" + hashlib.sha256(config.ca_pem.encode()).hexdigest(),
                            "identity": self._identity, "target_id": config.target_id, "profile": config.profile, "verifier_digest": config.verifier_digest}
        if config.pod_profile:
            self._descriptor["pod_profile"] = config.pod_profile

    def __repr__(self):
        return "Collector(configuration redacted)"

    def _get(self, path, deadline, *, missing_job=False, raw_output=False, byte_limit=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObservationError("collection time limit exceeded")
        connection = http.client.HTTPSConnection(self._host, self._port, context=self._tls,
                                                  timeout=min(self._config.timeout_seconds, remaining))
        watchdog = None
        try:
            connection.connect()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ObservationError("collection time limit exceeded")
            connected_socket = connection.sock
            def interrupt_read():
                try:
                    connected_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # Keep the captured socket even when http.client transfers ownership
            # to a Connection:close response. Shutdown interrupts slow headers/body.
            watchdog = threading.Timer(remaining, interrupt_read)
            watchdog.daemon = True
            watchdog.start()
            connection.request("GET", path, headers={"Authorization": "Bearer " + self._config.bearer_token,
                                                      "Accept": "application/json", "Connection": "close"})
            response = connection.getresponse()
            if response.status == 404 and missing_job:
                if time.monotonic() > deadline:
                    raise ObservationError("collection time limit exceeded")
                return None, b""
            if (response.status != 200 or response.getheader("Content-Encoding") not in (None, "identity")
                    or sum(len(k) + len(v) for k, v in response.getheaders()) > 32768):
                raise ObservationError("read response refused; effects remain unknown")
            limit = self._config.response_limit if byte_limit is None else min(byte_limit, self._config.response_limit)
            raw = bytearray()
            while len(raw) <= limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ObservationError("collection time limit exceeded")
                # read1 performs at most one buffered socket read. A peer cannot
                # extend a response indefinitely by trickling bytes below timeout.
                if connection.sock is not None:
                    connection.sock.settimeout(min(self._config.timeout_seconds, remaining))
                part = response.read1(min(65536, limit + 1 - len(raw)))
                if not part:
                    break
                raw.extend(part)
            if (len(raw) > limit or time.monotonic() > deadline
                    or self._config.bearer_token.encode() in raw):
                raise ObservationError("read response refused")
            if raw_output:
                return None, bytes(raw)
            value = loads(raw.decode("utf-8"))
            if type(value) is not dict:
                raise ObservationError("resource object required")
            return value, bytes(raw)
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise ObservationError("authenticated read failed; effects remain unknown") from None
        finally:
            if watchdog is not None:
                watchdog.cancel()
            connection.close()

    def _namespace(self, value):
        metadata = value.get("metadata", {})
        if (value.get("apiVersion") != "v1" or value.get("kind") != "Namespace" or type(metadata) is not dict
                or metadata.get("name") != self._config.namespace_name or metadata.get("uid") != self._config.namespace_uid
                or metadata.get("deletionTimestamp") is not None):
            raise ObservationError("namespace identity or lifecycle refused")

    def _match(self, raw_job, job, intent_json, report_json, remaining):
        # The binary and its containing directory must remain trusted and stable
        # during hash/exec. Hashing does not make an attacker-writable path safe.
        path = Path(self._config.verifier_path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 << 20:
            raise ObservationError("configured verifier file refused")
        with path.open("rb") as stream:
            binary_digest = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        if binary_digest != self._config.verifier_digest:
            raise ObservationError("configured verifier digest changed")
        metadata = job.get("metadata")
        if type(metadata) is not dict:
            raise ObservationError("Job metadata object required")
        version = metadata.get("resourceVersion")
        if type(version) is not str or not _TOKEN.fullmatch(version):
            raise ObservationError("Job resource version refused")
        with tempfile.TemporaryDirectory(prefix="dimaggi-object-check-") as directory:
            report = Path(directory) / "report.json"
            obj = Path(directory) / "object.json"
            report.write_bytes(report_json)
            obj.write_bytes(raw_job)
            with (Path(directory) / "result.json").open("w+b") as output:
                try:
                    result = subprocess.run([str(path), "--report", str(report), "--object", str(obj),
                                             "--expected-uid", self._config.job_uid, "--expected-resource-version", version,
                                             "--profile", self._config.profile], input=intent_json, stdout=output,
                                            stderr=subprocess.DEVNULL, timeout=max(0.001, remaining), check=False)
                except (OSError, subprocess.TimeoutExpired):
                    raise ObservationError("configured verifier failed") from None
                output.seek(0)
                raw = output.read(32769)
            if result.returncode != 0 or len(raw) > 32768:
                raise ObservationError("Job comparison refused")
            try:
                match = loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise ObservationError("Job comparison output refused") from None
        if (type(match) is not dict or match.get("schema") != "dimaggi-batch-object-match/v1"
                or match.get("matched") is not True or match.get("comparison_profile") != self._config.profile
                or match.get("uid") != self._config.job_uid or match.get("resource_version") != version
                or match.get("object_digest") != "sha256:" + hashlib.sha256(raw_job).hexdigest()
                or match.get("report_digest") != "sha256:" + hashlib.sha256(report_json).hexdigest()
                or match.get("execution_authorized") is not False or match.get("dispatch_possible") is not False
                or match.get("source_authenticated") is not False or match.get("permission") != "not_granted"):
            raise ObservationError("Job comparison commitments refused")
        return match

    def _runtime(self, job, pods, match, namespace_path, deadline):
        """Narrow runtime comparison; unsupported admission stays unverified.

        Only scheduler nodeName and the same three omitted false host flags are
        presentation differences. This is intentionally not generic Pod admission
        compatibility. Logs are read only for an attributed, stable zero-restart
        successful payload container and compared to the compiled stdout digest.
        """
        checks = []
        if job is None or match is None:
            return checks, False
        expected = job.get("spec", {}).get("template", {}).get("spec")
        if type(expected) is not dict:
            return checks, False
        expected = loads(canonical(expected).decode())
        for name in ("hostNetwork", "hostPID", "hostIPC"):
            if expected.get(name) is False:
                expected.pop(name)
        output_digest = match.get("expected_stdout_digest")
        output_limit = match.get("max_output_bytes")
        matched_success = False
        all_specs = True
        for pod in pods:
            metadata = pod.get("metadata", {}) if type(pod) is dict else {}
            pod_name = metadata.get("name")
            refs = metadata.get("ownerReferences", [])
            owners = [ref for ref in refs if type(ref) is dict and ref.get("controller") is True] if type(refs) is list else []
            if (type(pod_name) is not str or not _LABEL.fullmatch(pod_name)
                    or metadata.get("namespace") != self._config.namespace_name or len(owners) != 1
                    or any(owners[0].get(k) != v for k, v in {"apiVersion": "batch/v1", "kind": "Job",
                        "name": self._config.job_name, "uid": self._config.job_uid}.items())):
                raise ObservationError("Pod read scope or controller refused")
            actual = pod.get("spec")
            check = {"pod_uid": metadata.get("uid"), "spec_verified": False, "output_verified": False}
            checks.append(check)
            if type(actual) is not dict:
                all_specs = False
                continue
            actual = loads(canonical(actual).decode())
            node_present = "nodeName" in actual
            node = actual.pop("nodeName", None)
            if node_present and (type(node) is not str or len(node) > 253 or any(not _LABEL.fullmatch(part) for part in node.split("."))):
                all_specs = False
                continue
            for name in ("hostNetwork", "hostPID", "hostIPC"):
                if actual.get(name) is False:
                    actual.pop(name)
            if self._config.pod_profile == POD_PROFILE:
                # Explicit v1.35.0 stock admission profile; never strip unknown
                # fields or accept configurable non-stock toleration values.
                defaults = {"priority": 0, "preemptionPolicy": "PreemptLowerPriority",
                    "tolerations": [
                        {"key": "node.kubernetes.io/not-ready", "operator": "Exists",
                         "effect": "NoExecute", "tolerationSeconds": 300},
                        {"key": "node.kubernetes.io/unreachable", "operator": "Exists",
                         "effect": "NoExecute", "tolerationSeconds": 300}]}
                if (any(name in expected for name in defaults)
                        or "priorityClassName" in actual or "priorityClassName" in expected
                        or any(name not in actual or canonical(actual[name]) != canonical(value)
                               for name, value in defaults.items())):
                    all_specs = False
                    continue
                for name in defaults:
                    actual.pop(name)
            if canonical(actual) != canonical(expected):
                all_specs = False
                continue
            check["spec_verified"] = True
            status = pod.get("status", {})
            containers = expected.get("containers", [])
            states = status.get("containerStatuses", [])
            if (status.get("phase") != "Succeeded" or metadata.get("deletionTimestamp") is not None
                    or len(containers) != 1 or type(states) is not list or len(states) != 1
                    or states[0].get("name") != containers[0].get("name") or states[0].get("restartCount") != 0
                    or type(states[0].get("restartCount")) is not int
                    or states[0].get("state", {}).get("terminated", {}).get("exitCode") != 0):
                continue
            container_name = containers[0].get("name")
            if (type(container_name) is not str or not _LABEL.fullmatch(container_name)
                    or type(output_digest) is not str or not _SHA.fullmatch(output_digest)
                    or type(output_limit) is not int or not 1 <= output_limit <= 65536):
                continue
            pod_path = namespace_path + "/pods/" + pod_name
            before, _ = self._get(pod_path, deadline)
            if canonical(before) != canonical(pod):
                raise ObservationError("Pod changed before output collection")
            _, stdout = self._get(pod_path + "/log?" + urlencode({"container": container_name, "follow": "false", "timestamps": "false"}),
                                  deadline, raw_output=True, byte_limit=output_limit)
            after, _ = self._get(pod_path, deadline)
            if canonical(after) != canonical(pod):
                raise ObservationError("Pod changed during output collection")
            check["stdout_digest"] = "sha256:" + hashlib.sha256(stdout).hexdigest()
            check["stdout_bytes"] = len(stdout)
            check["output_verified"] = check["stdout_digest"] == output_digest
            matched_success |= check["output_verified"]
        return checks, bool(pods) and all_specs and matched_success

    def collect(self, store: ObservationStore, *, request_id: str, intent_json: bytes, report_json: bytes):
        """Acquire namespace/Job/Pod reads and append one observed workload event.

        The declared Job UID is an existing trusted pin, never learned by name
        from an ambiguous submission. Collection cannot settle an unknown attempt.
        """
        config = self._config
        if type(intent_json) is not bytes or type(report_json) is not bytes or max(len(intent_json), len(report_json)) > MAX_BYTES:
            raise ObservationError("bounded exact planning bytes required")
        registered = store.intent(request_id)
        if (registered["evidence_class"] != "observed" or registered["sources"]["workload"] != config.collector_id
                or registered["target_id"] != config.target_id or registered["workload_id"] != config.job_name):
            raise ObservationError("collector requires its registered observed workload source")
        try:
            planning, report = loads(intent_json.decode()), loads(report_json.decode())
            if (planning["request_id"] != request_id or report["report_id"] != registered["report_id"]
                    or planning["profile"] != registered["profile_id"]
                    or planning["cluster_id"] != config.cluster_id or planning["namespace"] != config.namespace_name
                    or planning["namespace_uid"] != config.namespace_uid):
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise ObservationError("planning bytes differ from registered application binding") from None
        started = self._clock()
        start_stamp = _stamp(started)
        if _utc(start_stamp) >= _utc(config.valid_until_utc):
            raise ObservationError("collector configuration expired")
        # Bound the materialized per-request history before decoding it. This
        # never projects/truncates history or removes records from the journal.
        history_count, history_bytes = store.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(body AS BLOB))), 0) FROM events WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if history_count >= MAX_HISTORY_EVENTS or history_bytes >= MAX_HISTORY_BYTES:
            raise ObservationError("collector history limit reached; archive remains intact")
        history = store.history(request_id)
        descriptor_digest = digest(self._descriptor)
        previous = [item["event"] for item in history if item["event"]["kind"] == "workload"
                    and item["event"]["source_id"] == config.collector_id]
        if any(_utc(item["observed_at_utc"]) > _utc(start_stamp) for item in previous):
            raise ObservationError("collector clock rolled back")
        for event in previous:
            if (event["source_epoch"] == config.source_epoch
                    and event["payload"].get("collection", {}).get("configuration_digest") != descriptor_digest):
                raise ObservationError("collector configuration changed inside a source epoch")
        sequence = 1 + max((event["source_sequence"] for event in previous if event["source_epoch"] == config.source_epoch), default=0)
        if sequence > 2**63 - 1:
            raise ObservationError("collector sequence exhausted")
        deadline = time.monotonic() + config.max_collection_seconds
        namespace_path = "/api/v1/namespaces/" + config.namespace_name
        job_path = "/apis/batch/v1/namespaces/" + config.namespace_name + "/jobs/" + config.job_name
        namespace, _ = self._get(namespace_path, deadline)
        self._namespace(namespace)
        job, raw_job = self._get(job_path, deadline, missing_job=True)
        match = None if job is None else self._match(raw_job, job, intent_json, report_json, deadline - time.monotonic())
        pods, raw_pods = self._get(namespace_path + "/pods?" + urlencode({"labelSelector": "batch.kubernetes.io/controller-uid=" + config.job_uid, "limit": "1000"}), deadline)
        if (pods.get("apiVersion") != "v1" or pods.get("kind") != "PodList" or type(pods.get("metadata")) is not dict
                or not isinstance(pods["metadata"].get("resourceVersion"), str)
                or not pods["metadata"]["resourceVersion"] or pods["metadata"].get("continue", "") != ""
                or type(pods["metadata"].get("remainingItemCount", 0)) is not int
                or pods["metadata"].get("remainingItemCount", 0) != 0 or type(pods.get("items")) is not list
                or len(pods["items"]) > 1000):
            raise ObservationError("complete bounded Pod list required")
        # Kubernetes' typed list serializer omits per-item TypeMeta. Supply only
        # absent fields under the validated v1 PodList, preserving raw-byte hash.
        # Explicit null/foreign values refuse; named Pod GETs remain unchanged.
        for item in pods["items"]:
            if type(item) is not dict:
                raise ObservationError("Pod list item must be an object")
            for key, value in (("apiVersion", "v1"), ("kind", "Pod")):
                if key in item and item[key] != value:
                    raise ObservationError("Pod list item type refused")
                item.setdefault(key, value)
        preliminary = {"schema": SCHEMA, "evidence_class": "observed", "collector_id": config.collector_id,
            "source_epoch": config.source_epoch, "source_sequence": sequence, "observed_at_utc": _stamp(self._clock()),
            **{key: registered[key] for key in IDENTITIES}, "identity": self._identity,
            "job": job, "pods": pods["items"], "pods_complete": True, "absence": None}
        _kubernetes_event(canonical(preliminary), intent=registered, expected_collector_id=config.collector_id,
                          expected_identity=self._identity, evidence_class="observed")
        runtime_checks, runtime_verified = self._runtime(job, pods["items"], match, namespace_path, deadline)
        job_after, _ = self._get(job_path, deadline, missing_job=True)
        namespace_after, _ = self._get(namespace_path, deadline)
        self._namespace(namespace_after)
        if canonical(job_after) != canonical(job):
            raise ObservationError("Job changed during collection; take a new read without retrying submission")
        finished = self._clock()
        stamp = _stamp(finished)
        if (finished < started or (finished - started).total_seconds() > config.max_collection_seconds
                or _utc(stamp) >= _utc(config.valid_until_utc) or time.monotonic() > deadline):
            raise ObservationError("collection clock, freshness or configuration refused")
        bundle = {"schema": SCHEMA, "evidence_class": "observed", "collector_id": config.collector_id,
                  "source_epoch": config.source_epoch, "source_sequence": sequence, "observed_at_utc": stamp,
                  **{key: registered[key] for key in IDENTITIES}, "identity": self._identity,
                  "job": job, "pods": pods["items"], "pods_complete": True,
                  "absence": None if job is not None else {"kind": "job_get_not_found", "http_status": 404, "observed_at_utc": stamp}}
        event = _kubernetes_event(canonical(bundle), intent=registered, expected_collector_id=config.collector_id,
                                  expected_identity=self._identity, evidence_class="observed")
        event["payload"]["collection"] = {"schema": "dimaggi-tls-collection/v1", "source_authenticated": True,
            "authentication_scope": "configured TLS API origin inside trusted collector process",
            "configuration_digest": descriptor_digest, "started_at_utc": start_stamp, "finished_at_utc": stamp,
            "pod_list_resource_version": pods["metadata"]["resourceVersion"], "job_match": match,
            "pod_list_raw_digest": "sha256:" + hashlib.sha256(raw_pods).hexdigest(),
            "pod_list_type_metadata": "absent item apiVersion/kind supplied from validated v1 PodList",
            "pod_comparison_profile": config.pod_profile or "exact-job-template",
            "runtime_checks": runtime_checks,
            "pod_spec_verified": bool(runtime_checks) and all(item["spec_verified"] for item in runtime_checks),
            "output_verified": runtime_verified}
        # Pod status is attributable but not proof of exact runtime behavior:
        # standalone Pod admission and stdout are not covered by a Job match.
        if event["state"] == "succeeded" and not runtime_verified:
            event["state"] = "unknown"
            event["payload"]["interpretation_reasons"].append("runtime_pod_spec_and_output_not_verified")
        event_bytes = len(canonical(event))
        if event_bytes > MAX_BYTES or history_bytes + event_bytes > MAX_HISTORY_BYTES:
            raise ObservationError("combined collection exceeds application input bound")
        return _persist_event(store, event, expected_identity=self._identity, recorded_at_utc=stamp)
