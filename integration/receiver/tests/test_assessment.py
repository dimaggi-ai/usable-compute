from copy import deepcopy
import pytest
from test_infrastructure import fixture, NOW
from test_telemetry import CSV
from dimaggi_receiver.assessment import assess
from dimaggi_receiver.telemetry import nvidia_smi
from dimaggi_receiver.source_watch import collect, compare, review_queue
from dimaggi_receiver.jsonio import digest


def setup():
    r, q = fixture.fixtures()
    p = q["pools"][0]
    profile = r["profiles"][0]
    profile.update(provider="nvidia", device_unit="gpu")
    profile["stack"]["driver"] = "580.1"
    p.update(device_unit="gpu")
    p["stack"]["driver"] = "580.1"
    config = {
        "schema": "dimaggi-source-watch-config/v1",
        "sources": [
            {
                "id": "smi",
                "url": "https://docs.nvidia.com/deploy/nvidia-smi/",
                "profile_ids": [profile["id"]],
                "max_bytes": 1000,
            }
        ],
    }
    source = collect(config, NOW, lambda *a, **k: (b"fixture", "text/html"))
    queue = review_queue(config, compare(config, source, source))
    meta = dict(
        source_id=p["source_id"],
        source_epoch=p["epoch"],
        target_id=p["target_id"],
        observed_at=p["observed_at"],
        evidence_class=p["evidence_class"],
    )
    s = nvidia_smi(CSV, meta, NOW)
    e = dict(
        schema="dimaggi-assessment-evidence/v1",
        telemetry=[
            dict(
                pool_id=p["id"],
                snapshot=s,
                snapshot_digest=s["snapshot_digest"],
                device_mapping={s["devices"][0]["id"]: "physical-1"},
            )
        ],
        source_queue=queue,
    )
    return r, q, e, config, source


def run(r, q, e):
    return assess(r, q, digest(r), e, digest(e), NOW)


def test_joined_memory_plan_and_source_quarantine():
    r, q, e, c, source = setup()
    result = run(r, q, e)
    assert result["status"] == "compatible" and not result["execution_authorized"]
    assert (
        result["plan"]["status"] == "compatible"
        and result["memory_screens"][0]["status"] == "pass"
    )
    changed = collect(c, NOW, lambda *a, **k: (b"changed", "text/html"))
    e["source_queue"] = review_queue(c, compare(c, source, changed), e["source_queue"])
    assert run(r, q, e)["refusals"][0]["reason"] == "source_review_pending"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "driver",
        "memory",
        "inventory",
        "unwatched",
        "partition",
        "stale_queue",
        "foreign",
        "epoch",
        "pin",
        "tampered",
    ],
)
def test_adversarial_joins(case):
    r, q, e, c, source = setup()
    entry = e["telemetry"][0]
    s = entry["snapshot"]
    if case == "missing":
        e["telemetry"] = []
    elif case == "driver":
        q["pools"][0]["stack"]["driver"] = r["profiles"][0]["stack"]["driver"] = "999.1"
    elif case == "memory":
        q["workloads"][0]["memory_bytes_per_device"] = 900 * 1024**2
        r["profiles"][0]["memory_bytes_per_device"] = 1024**3
    elif case == "inventory":
        q["workloads"][0]["resources"]["devices"] = 2
    elif case == "unwatched":
        e["source_queue"]["profile_ids"] = ["other"]
        e["source_queue"]["queue_digest"] = digest(
            {k: v for k, v in e["source_queue"].items() if k != "queue_digest"}
        )
    elif case == "partition":
        r["profiles"][0]["partition"] = q["pools"][0]["partition"] = "partition-unknown"
    elif case == "stale_queue":
        e["source_queue"]["observed_at"] = "2026-09-19T12:00:00Z"
        e["source_queue"]["queue_digest"] = digest(
            {k: v for k, v in e["source_queue"].items() if k != "queue_digest"}
        )
    elif case == "foreign":
        q["pools"][0]["target_id"] = "foreign"
    elif case == "epoch":
        q["pools"][0]["epoch"] = "two"
    elif case == "pin":
        entry["snapshot_digest"] = digest("foreign")
    elif case == "tampered":
        s["devices"][0]["memory_free_bytes"] = 2**40
    if case in {"stale_queue", "foreign", "epoch", "pin", "tampered"}:
        with pytest.raises(ValueError):
            run(r, q, e)
    else:
        assert run(r, q, e)["status"] == "refused"


def test_installed_cli_replays_join_and_preserves_refusal(tmp_path):
    import subprocess, sys, json, os

    r, q, e, c, source = setup()
    for name, value in [("registry", r), ("request", q), ("evidence", e)]:
        (tmp_path / (name + ".json")).write_text(json.dumps(value))
    cmd = [
        sys.executable,
        "-m",
        "dimaggi_receiver.cli",
        "infrastructure-assess",
        "--registry",
        str(tmp_path / "registry.json"),
        "--registry-digest",
        digest(r),
        "--input",
        str(tmp_path / "request.json"),
        "--as-of",
        NOW,
        "--evidence",
        str(tmp_path / "evidence.json"),
        "--evidence-digest",
        digest(e),
    ]
    p = subprocess.run(cmd, capture_output=True, timeout=20)
    assert p.returncode == 0 and json.loads(p.stdout)["status"] == "compatible"
    cmd[-1] = digest("wrong")
    p = subprocess.run(cmd, capture_output=True, timeout=20)
    assert p.returncode == 2 and not json.loads(p.stdout)["execution_authorized"]


def test_planning_refusal_preserves_unassessed_telemetry():
    r, q, e, c, source = setup()
    q["workloads"][0]["resources"]["cpu_millicores"] = 99999
    result = run(r, q, e)
    assert result["status"] == "refused" and result["refusals"] == [
        {"reason": "planning_refused"}
    ]
    assert not result["memory_screens"] and result["unassessed_pool_ids"] == ["pool-1"]


def test_both_published_provider_replays_recompute():
    from pathlib import Path
    from dimaggi_receiver.jsonio import loads

    root = Path(__file__).parents[1] / "examples/operations"
    for provider in ("google", "nvidia"):
        read = lambda name: loads((root / provider / (name + ".json")).read_text())
        pins = read("pins")
        result = assess(
            read("registry"),
            read("request"),
            pins["registry_digest"],
            read("evidence"),
            pins["evidence_digest"],
            pins["as_of"],
        )
        assert result == read("assessment") and result["status"] == "compatible"
        assert result["plan"]["evidence_class"] == "synthetic"
