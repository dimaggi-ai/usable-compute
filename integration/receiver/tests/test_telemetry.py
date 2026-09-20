from copy import deepcopy
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
import pytest
from dimaggi_receiver import telemetry as t, telemetry_collect as c
from dimaggi_receiver.jsonio import canonical, digest

NOW = "2026-09-20T12:00:00Z"
META = dict(
    source_id="test-source",
    source_epoch="one",
    target_id="test-host",
    observed_at=NOW,
    evidence_class="synthetic",
)
IDENTITY = dict(project_id="fixture-project", zone="us-central1-a", instance_id="123")
CSV = b"GPU-01234567-abcd, NVIDIA Test GPU, 580.1, 1024, 256, 768, 20.125, 100, Disabled\n"


def body():
    rows = []
    for metric, value in [("memory_total", 1024), ("memory_used", 256)]:
        rows.append(
            dict(
                metric={
                    "type": t.TPU_PREFIX + metric,
                    "labels": {"accelerator_id": "0"},
                },
                resource={"type": "gce_instance", "labels": dict(IDENTITY)},
                metricKind="GAUGE",
                valueType="INT64",
                unit="By",
                points=[
                    {"interval": {"endTime": NOW}, "value": {"int64Value": str(value)}}
                ],
            )
        )
    return {"timeSeries": rows, "unit": "By"}


def test_known_provider_units_and_screen():
    n = t.nvidia_smi(CSV, META, NOW)
    d = n["devices"][0]
    assert d["memory_total_bytes"] == 1073741824 and d["memory_free_bytes"] == 805306368
    assert d["power_milliwatts"] == 20125 and d["device_unit"] == "gpu"
    g = t.google_tpu(canonical(body()), META, IDENTITY, NOW)
    assert (
        g["devices"][0]["memory_free_bytes"] == 768
        and g["devices"][0]["device_unit"] == "runtime_device"
    )
    for s in (n, g):
        mapping = {d["id"]: "physical-1" for d in s["devices"]}
        assert (
            t.memory_screen(s, s["snapshot_digest"], META, mapping, 128, NOW)["status"]
            == "pass"
        )
        assert (
            t.memory_screen(s, s["snapshot_digest"], META, mapping, 2**40, NOW)[
                "status"
            ]
            == "refused"
        )
        with pytest.raises(ValueError):
            t.memory_screen(
                s,
                s["snapshot_digest"],
                dict(META, source_epoch="two"),
                mapping,
                128,
                NOW,
            )


@pytest.mark.parametrize("replacement", [b"Enabled", b"N/A", b"[Not Supported]"])
def test_mig_mode_cannot_imply_whole_gpu(replacement):
    s = t.nvidia_smi(CSV.replace(b"Disabled", replacement), META, NOW)
    result = t.memory_screen(
        s, s["snapshot_digest"], META, {s["devices"][0]["id"]: "physical-1"}, 128, NOW
    )
    assert result["status"] == "refused"


@pytest.mark.parametrize(
    "raw",
    [
        CSV + CSV,
        CSV.replace(b"1024", b"1"),
        CSV.replace(b"20.125", b"1e1000000"),
        CSV.replace(b"20.125", b"NaN"),
        CSV.replace(b"580.1", b"unknown"),
        CSV.replace(b"Disabled", b"surprise"),
        CSV + b"garbage",
        b"",
        b"\xff",
    ],
)
def test_bad_smi_inputs(raw):
    with pytest.raises((ValueError, UnicodeError)):
        t.nvidia_smi(raw, META, NOW)


def test_missing_gpu_memory_is_not_zero():
    s = t.nvidia_smi(CSV.replace(b"768", b"N/A"), META, NOW)
    assert s["devices"][0]["memory_free_bytes"] is None
    assert (
        t.memory_screen(
            s, s["snapshot_digest"], META, {s["devices"][0]["id"]: "physical-1"}, 1, NOW
        )["status"]
        == "refused"
    )


@pytest.mark.parametrize(
    "case",
    [
        "next_page",
        "errors",
        "unreachable",
        "units",
        "foreign",
        "duplicate",
        "missing_pair",
        "float",
        "negative",
        "too_used",
        "stale",
        "different_timestamp",
        "aggregated",
        "duplicate_point",
        "wrong_metric",
    ],
)
def test_google_failures(case):
    b = body()
    s = b["timeSeries"][0]
    if case == "next_page":
        b["nextPageToken"] = "more"
    elif case == "errors":
        b["executionErrors"] = [{"code": 13}]
    elif case == "unreachable":
        b["unreachable"] = ["region"]
    elif case == "units":
        s["unit"] = "GB"
    elif case == "foreign":
        s["resource"]["labels"]["instance_id"] = "456"
    elif case == "duplicate":
        b["timeSeries"].append(deepcopy(s))
    elif case == "missing_pair":
        b["timeSeries"] = b["timeSeries"][:1]
    elif case == "float":
        s["points"][0]["value"] = {"doubleValue": 1024.0}
    elif case == "negative":
        s["points"][0]["value"]["int64Value"] = "-1"
    elif case == "too_used":
        b["timeSeries"][1]["points"][0]["value"]["int64Value"] = "1025"
    elif case == "stale":
        s["points"][0]["interval"]["endTime"] = "2026-09-19T12:00:00Z"
    elif case == "different_timestamp":
        s["points"][0]["interval"]["endTime"] = "2026-09-20T11:59:59Z"
    elif case == "aggregated":
        s["metricKind"] = "DELTA"
    elif case == "duplicate_point":
        s["points"].append(deepcopy(s["points"][0]))
    elif case == "wrong_metric":
        s["metric"]["type"] = t.TPU_PREFIX + "active_chips"
    with pytest.raises(ValueError):
        t.google_tpu(canonical(b), META, IDENTITY, NOW)


def test_google_collector_pagination_and_no_credential_export():
    calls = []

    def fetch(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        calls.append(query)
        if "memory_total" in query["filter"][0]:
            if not query.get("pageToken"):
                return (
                    canonical({"timeSeries": [], "nextPageToken": "page-2"}),
                    "application/json",
                )
            return (
                canonical({"timeSeries": [body()["timeSeries"][0]], "unit": "By"}),
                "application/json",
            )
        return (
            canonical({"timeSeries": [body()["timeSeries"][1]], "unit": "By"}),
            "application/json",
        )

    s = c.collect_google(
        IDENTITY, "secret-fixture", META, NOW, "2026-09-20T11:55:00Z", fetch=fetch
    )
    assert len(calls) == 3 and "secret-fixture" not in json.dumps(s)
    assert s["devices"][0]["memory_free_bytes"] == 768


def test_google_cycle_and_partial_collection_refuse():
    for b in (
        {"nextPageToken": "again"},
        {"executionErrors": [{"code": 13}]},
        {"unreachable": ["x"]},
        {"unit": "wrong"},
    ):
        with pytest.raises(ValueError):
            c.collect_google(
                IDENTITY,
                "fixture",
                META,
                NOW,
                "2026-09-20T11:55:00Z",
                fetch=lambda *a, **k: (canonical(b), "application/json"),
            )


def test_live_command_uses_exact_read_only_argv(tmp_path):
    # This is a synthetic executable, never a hardware-validation claim.
    import os

    binary = tmp_path / "smi-fixture"
    binary.write_text(
        '#!/bin/sh\n[ "$1" = "--query-gpu='
        + t.SMI_FIELDS
        + '" ] || exit 9\n[ "$2" = "--format=csv,noheader,nounits" ] || exit 9\nprintf "'
        + CSV.decode().strip()
        + '\\n"\n'
    )
    binary.chmod(0o700)
    pin = "sha256:" + hashlib.sha256(binary.read_bytes()).hexdigest()
    assert (
        c.collect_nvidia(str(binary), pin, META, NOW)["devices"][0]["power_milliwatts"]
        == 20125
    )
    with pytest.raises(ValueError):
        c.collect_nvidia(str(binary), digest("wrong"), META, NOW)


def test_stale_and_tampered_snapshots():
    with pytest.raises(ValueError):
        t.nvidia_smi(CSV, META, "2026-09-20T12:05:00Z")
    s = t.nvidia_smi(CSV, META, NOW)
    s["devices"][0]["memory_free_bytes"] = 2**40
    with pytest.raises(ValueError):
        t.memory_screen(
            s, s["snapshot_digest"], META, {s["devices"][0]["id"]: "physical-1"}, 1, NOW
        )


def test_monitoring_nanoseconds_and_equivalent_time_duplicates():
    b = body()
    for s in b["timeSeries"]:
        s["points"][0]["interval"]["endTime"] = "2026-09-20T11:59:59.999999999Z"
    s = t.google_tpu(canonical(b), META, IDENTITY, NOW)
    assert (
        t.memory_screen(s, s["snapshot_digest"], META, {"0": "physical-1"}, 128, NOW)[
            "status"
        ]
        == "pass"
    )
    b = body()
    point = deepcopy(b["timeSeries"][0]["points"][0])
    point["interval"]["endTime"] = NOW.replace("Z", ".000000000Z")
    b["timeSeries"][0]["points"].append(point)
    with pytest.raises(ValueError):
        t.google_tpu(canonical(b), META, IDENTITY, NOW)


def test_token_permissions_symlinks_and_no_ambient_credentials(tmp_path):
    token = tmp_path / "token"
    token.write_text("fixture-only\n")
    token.chmod(0o600)
    assert c.read_token(token) == "fixture-only"
    token.chmod(0o644)
    with pytest.raises(ValueError):
        c.read_token(token)
    token.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(token)
    with pytest.raises(OSError):
        c.read_token(link)
