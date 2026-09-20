"""Opt-in loopback lab faults with the real TENWA adapter and Kubernetes API.

The caller supplies an already approved local lab configuration and fresh pinned
infrastructure inputs. This tool creates bounded CPU Jobs, kills its own adapter
process in two cases, and deletes only the exact UID it captured. Never use with
production: only https://127.0.0.1 endpoints and kind contexts are accepted.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path
import socket
import ssl
import subprocess
import threading
from urllib.parse import urlsplit
from dimaggi_receiver.infrastructure import plan, cpu_binding
from dimaggi_receiver.jsonio import loads, read_file, dumps, digest


def run(a):
    host = loads(read_file(a.config).decode())
    endpoint = urlsplit(host["Endpoint"])
    if (
        endpoint.scheme != "https"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
        or endpoint.username
        or not a.context.startswith("kind-")
    ):
        raise ValueError("explicit loopback kind lab required")
    root = a.output.resolve()
    for repository in (a.tenwa_root.resolve(), Path(__file__).resolve().parents[3]):
        if root == repository or repository in root.parents:
            raise ValueError("private output must be outside repositories")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    root.chmod(0o700)
    registry = loads(read_file(a.registry).decode())
    request = loads(read_file(a.request).decode())
    if request["evidence_class"] != "local_lab":
        raise ValueError("real local lab inputs required")
    k = [
        str(a.kubectl),
        "--kubeconfig",
        str(a.kubeconfig),
        "--context",
        a.context,
        "--request-timeout=15s",
        "-n",
        host["Deployment"]["Namespace"],
    ]

    def command(argv, timeout=30):
        return subprocess.run(
            list(map(str, argv)), capture_output=True, timeout=timeout
        )

    def save(path, value):
        path.write_text(dumps(value))
        path.chmod(0o600)

    records = []
    for mode in (
        "lost_response",
        "crash_after_create",
        "crash_before_create",
        "request_timeout",
    ):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        q = deepcopy(request)
        q["request_id"] = (
            "fault-"
            + mode.replace("_", "-")
            + "-"
            + datetime.now(timezone.utc).strftime("%H%M%S")
        )
        p = plan(registry, q, digest(registry), now)
        if p["status"] != "compatible":
            raise ValueError("fresh compatible infrastructure required")
        binding = cpu_binding(registry, q, digest(registry), p, now)
        case = root / mode
        case.mkdir(mode=0o700)
        key, cert = case / "proxy.key", case / "proxy.pem"
        result = command(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                key,
                "-out",
                cert,
                "-days",
                "1",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ]
        )
        if result.returncode:
            raise ValueError("ephemeral loopback TLS setup failed")
        key.chmod(0o600)
        cert.chmod(0o600)
        reached, release = threading.Event(), threading.Event()
        record = dict(
            case=mode,
            request_id=q["request_id"],
            calls=0,
            upstream_uid=None,
            forwarded=False,
        )
        upstream_tls = ssl.create_default_context(cafile=host["CAFile"])
        expected_path = (
            "/apis/batch/v1/namespaces/" + host["Deployment"]["Namespace"] + "/jobs"
        )
        expected_bytes = None

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    self.connection.settimeout(5)
                    if (
                        self.path != expected_path
                        or self.headers.get("Transfer-Encoding")
                        or self.headers.get("Content-Type") != "application/json"
                    ):
                        raise ValueError("proxy scope refused")
                    count = int(self.headers.get("Content-Length", "0"))
                    if not 0 < count <= 16384:
                        raise ValueError("proxy input bound")
                    body = self.rfile.read(count)
                    if body != expected_bytes:
                        raise ValueError("proxy exact Job bytes refused")
                    record["calls"] += 1
                    if record["calls"] != 1:
                        raise ValueError("proxy duplicate refused")
                    if mode in ("crash_before_create", "request_timeout"):
                        reached.set()
                        release.wait(10)
                    else:
                        conn = http.client.HTTPSConnection(
                            endpoint.hostname,
                            endpoint.port or 443,
                            context=upstream_tls,
                            timeout=5,
                        )
                        try:
                            conn.request(
                                "POST",
                                expected_path,
                                body=body,
                                headers={
                                    "Authorization": self.headers["Authorization"],
                                    "Content-Type": "application/json",
                                },
                            )
                            response = conn.getresponse()
                            raw = response.read((1 << 20) + 1)
                            if response.status != 201 or len(raw) > 1 << 20:
                                raise ValueError("upstream creation failed")
                            job = loads(raw.decode())
                            record["upstream_uid"] = job["metadata"]["uid"]
                            record["forwarded"] = True
                            save(case / "upstream-job.json", job)
                        finally:
                            conn.close()
                        reached.set()
                        if mode == "crash_after_create":
                            release.wait(10)
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                except Exception as exc:
                    record["proxy_error"] = type(exc).__name__
                    reached.set()
                    self.close_connection = True

        server = HTTPServer(("127.0.0.1", 0), Proxy)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cert, key)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        cfg = deepcopy(host)
        cfg["Endpoint"] = "https://127.0.0.1:" + str(server.server_port)
        cfg["CAFile"] = str(cert)
        cfg["TimeoutSeconds"] = 2 if mode == "request_timeout" else 15
        raw = dumps(binding)
        cfg["Deployment"].update(
            InfrastructureEvidence=raw,
            InfrastructureEvidenceDigest="sha256:"
            + hashlib.sha256(raw.encode()).hexdigest(),
            ValidUntil=binding["valid_until"],
        )
        save(case / "host.json", cfg)
        save(case / "request.json", q)
        save(case / "plan.json", p)
        work = case / "campaign"
        proc = None
        created_uid = None
        try:
            prepared = command(
                [
                    a.runner,
                    "--config",
                    case / "host.json",
                    "--evidence",
                    a.evidence,
                    "--adapter",
                    a.adapter,
                    "--work-dir",
                    work,
                    "--repo-root",
                    a.tenwa_root,
                    "--request-id",
                    q["request_id"],
                ]
            )
            if prepared.returncode:
                raise ValueError("campaign preparation failed")
            expected_bytes = (work / "job.json").read_bytes()
            job_name = loads(expected_bytes.decode())["metadata"]["name"]
            record["job_name"] = job_name
            absent = command(
                k + ["get", "job", job_name, "--ignore-not-found", "-o", "json"]
            )
            if absent.returncode or absent.stdout.strip():
                raise ValueError("fresh Job name required")
            args = [
                str(a.adapter),
                "--config",
                str(work / "config.json"),
                "--intent",
                str(work / "intent.json"),
                "--report",
                str(work / "report.json"),
                "--grant",
                str(work / "grant.json"),
            ]

            def adapter(mode):
                return command(args + ["--mode", mode])

            anchor = adapter("provision")
            if anchor.returncode:
                raise ValueError("ledger provisioning refused")
            actual = loads((work / "config.json").read_text())
            actual["Anchor"] = loads(anchor.stdout.decode())
            save(work / "config.json", actual)
            proc = subprocess.Popen(
                args + ["--mode", "execute"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if not reached.wait(20):
                raise ValueError("fault barrier not reached")
            if "proxy_error" in record:
                raise ValueError("fault proxy refused")
            if mode.startswith("crash_"):
                proc.kill()
            stdout, stderr = proc.communicate(timeout=20)
            record["adapter_exit"] = proc.returncode
            (case / "adapter-result.json").write_bytes(stdout)
            (case / "adapter-diagnostic.txt").write_bytes(stderr)
            release.set()
            inspected = adapter("inspect")
            if inspected.returncode:
                raise ValueError("retained ledger unreadable")
            ledger = loads(inspected.stdout.decode())
            save(case / "ledger-before-reopen.json", ledger)
            duplicate = adapter("execute")
            record["duplicate_exit"] = duplicate.returncode
            if duplicate.returncode != 2:
                raise ValueError("duplicate was not refused")
            reopened = adapter("inspect")
            if reopened.returncode:
                raise ValueError("reopened ledger unreadable")
            ledger = loads(reopened.stdout.decode())
            save(case / "ledger-after-reopen.json", ledger)
            snapshots = ledger.get("snapshots", ledger.get("Snapshots"))
            if not snapshots or len(snapshots) != 1:
                raise ValueError("one durable attempt required")
            retained = snapshots[0]
            if (
                retained["remote_attempt"] != "unknown"
                or retained["workload_outcome"] != "not_observed"
                or retained["execution_authorized"]
                or retained["dispatch_possible"]
            ):
                raise ValueError("uncertainty promoted to authority")
            record["retained_state"] = retained["state"]
            record["remote_attempt"] = retained["remote_attempt"]
            observed = command(
                k + ["get", "job", job_name, "--ignore-not-found", "-o", "json"]
            )
            if observed.returncode:
                raise ValueError("Job observation failed")
            if record["forwarded"]:
                job = loads(observed.stdout.decode())
                created_uid = job["metadata"]["uid"]
                if created_uid != record["upstream_uid"]:
                    raise ValueError("Job replaced")
                completed = command(
                    k
                    + [
                        "wait",
                        "--for=condition=complete",
                        "job/" + job_name,
                        "--timeout=75s",
                    ],
                    timeout=90,
                )
                if completed.returncode:
                    raise ValueError("remote workload did not complete")
                logs = command(
                    k
                    + [
                        "logs",
                        "job/" + job_name,
                        "--container=payload",
                        "--timestamps=false",
                    ]
                )
                if (
                    logs.returncode
                    or logs.stdout != (work / "expected-stdout.txt").read_bytes()
                ):
                    raise ValueError("remote output differs")
                (case / "observed-stdout.txt").write_bytes(logs.stdout)
                save(
                    case / "observed-job.json",
                    loads(
                        command(
                            k + ["get", "job", job_name, "-o", "json"]
                        ).stdout.decode()
                    ),
                )
                record["remote_workload"] = "succeeded_with_exact_output"
            else:
                if observed.stdout.strip():
                    raise ValueError("unexpected remote Job")
                record["remote_workload"] = "absent_at_observation"
            if record["calls"] != 1:
                raise ValueError("retry crossed transport")
            record["status"] = "passed"
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            # Revoke every campaign key; no positive authority survives the fault.
            auth_path = work / "authority.json"
            if auth_path.exists():
                auth = loads(auth_path.read_text())
                for value in auth["Keys"].values():
                    value["Revoked"] = True
                save(auth_path, auth)
            if created_uid:
                current = command(k + ["get", "job", record["job_name"], "-o", "json"])
                if (
                    current.returncode == 0
                    and loads(current.stdout.decode())["metadata"]["uid"] == created_uid
                ):
                    deletion = case / "delete-options.json"
                    save(
                        deletion,
                        {
                            "apiVersion": "v1",
                            "kind": "DeleteOptions",
                            "preconditions": {"uid": created_uid},
                            "propagationPolicy": "Foreground",
                        },
                    )
                    deleted = command(
                        k
                        + [
                            "delete",
                            "--raw",
                            expected_path + "/" + record["job_name"],
                            "-f",
                            str(deletion),
                        ]
                    )
                    if deleted.returncode != 0:
                        raise ValueError(
                            "UID-bound cleanup refused; retain lab evidence"
                        )
                    waited = command(
                        k
                        + [
                            "wait",
                            "--for=delete",
                            "job/" + record["job_name"],
                            "--timeout=20s",
                        ]
                    )
                    if waited.returncode != 0:
                        raise ValueError("cleanup not complete")
                    record["cleanup_exit"] = deleted.returncode
            save(case / "receipt.json", record)
        records.append(record)
    save(
        root / "result.json",
        dict(
            schema="dimaggi-local-transport-faults/v1",
            cases=records,
            evidence_class="local_lab",
            independent_acceptance=False,
            execution_authorized=False,
        ),
    )
    return records


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in (
        "config",
        "registry",
        "request",
        "evidence",
        "runner",
        "adapter",
        "tenwa-root",
        "output",
        "kubectl",
        "kubeconfig",
    ):
        p.add_argument("--" + name, required=True, type=Path)
    p.add_argument("--context", required=True)
    a = p.parse_args()
    print(dumps({"cases": run(a), "execution_authorized": False}), end="")


if __name__ == "__main__":
    main()
