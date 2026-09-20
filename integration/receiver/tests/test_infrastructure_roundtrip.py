"""Installed receiver → actual compiled Go planner. Explicit private binary required."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import pytest
from dimaggi_receiver.infrastructure import plan, cpu_binding
from dimaggi_receiver.jsonio import digest, dumps

spec = importlib.util.spec_from_file_location(
    "infra_fixture_roundtrip",
    Path(__file__).parents[1] / "examples/infrastructure/generate.py",
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_installed_python_to_go_binding(tmp_path):
    binary = os.environ.get("DIMAGGI_BATCH_JOB_PLAN")
    if not binary:
        pytest.skip("explicit compiled TENWA batch-job-plan required")
    r, q = fixture.fixtures()
    now = "2026-09-20T12:00:01Z"
    # Test-only assertions, never hardware observations or a deployable packet.
    r["profiles"][0]["validation_class"] = q["evidence_class"] = q["pools"][0][
        "evidence_class"
    ] = q["budgets"][0]["evidence_class"] = "local_lab"
    p = plan(r, q, digest(r), now)
    b = cpu_binding(r, q, digest(r), p, now)
    data = dumps(b).encode()
    path = tmp_path / "binding.json"
    path.write_bytes(data)
    pin = "sha256:" + hashlib.sha256(data).hexdigest()
    intent = dict(
        schema="dimaggi-cpu-job-intent/v1",
        profile="cpu-lab-payload/v1",
        request_id=q["request_id"],
        report_digest=digest("test report"),
        evidence_digest=digest("test evidence"),
        cluster_id=b["cluster_id"],
        namespace="dimaggi-lab",
        namespace_uid=b["namespace_uid"],
        service_account="payload",
        image="registry.invalid/dimaggi/cpu@" + digest("test image"),
        architecture=b["architecture"],
        cpu_millicores=b["cpu_millicores"],
        memory_bytes=b["memory_bytes"],
        ephemeral_storage_bytes=b["ephemeral_storage_bytes"],
        deadline_seconds=60,
        backoff_limit=0,
    )

    def invoke(at=now, pinned=pin):
        return subprocess.run(
            [
                binary,
                "--infrastructure-binding",
                str(path),
                "--infrastructure-digest",
                pinned,
                "--as-of",
                at,
            ],
            input=dumps(intent),
            text=True,
            capture_output=True,
            timeout=10,
        )

    result = invoke()
    assert result.returncode == 0, result.stderr
    compiled = json.loads(result.stdout)
    assert not compiled["execution_ready"] and compiled["permission"] == "not_granted"
    assert invoke("2026-09-20T12:05:00Z").returncode == 2
    assert invoke(pinned=digest("wrong")).returncode == 2
    intent["request_id"] = "another-request"
    assert invoke().returncode == 2
    intent["request_id"] = q["request_id"]
    intent["memory_bytes"] += 1
    assert invoke().returncode == 2
    intent["memory_bytes"] -= 1
    b["evidence_class"] = "synthetic"
    data = dumps(b).encode()
    path.write_bytes(data)
    assert invoke(pinned="sha256:" + hashlib.sha256(data).hexdigest()).returncode == 2
