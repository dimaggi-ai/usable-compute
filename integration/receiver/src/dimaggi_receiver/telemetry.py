"""Versioned read-only telemetry adapters. Telemetry is not a reservation.

NVIDIA SMI fixed-query CSV and Google Monitoring raw GAUGE/INT64 memory series
are normalized without inventing stack, health, topology or physical-chip counts.
"""

from __future__ import annotations
import csv
import hashlib
import io
import re
from copy import deepcopy
from .infrastructure import array, integer, obj, sha, stamp, text
from .jsonio import MAX_BYTES, canonical, digest, loads
from .quantities import normalize_quantity

SMI_FIELDS = "uuid,name,driver_version,memory.total,memory.used,memory.free,power.draw,power.limit,mig.mode.current"
TPU_PREFIX = "compute.googleapis.com/instance/tpu/accelerator/"
META_KEYS = ("source_id", "source_epoch", "target_id", "observed_at", "evidence_class")


def metadata(meta, as_of, freshness):
    obj(meta, META_KEYS, "telemetry metadata")
    for k in ("source_id", "source_epoch", "target_id"):
        text(meta[k], k)
    if meta["evidence_class"] not in {"synthetic", "hardware_observed", "local_lab"}:
        raise ValueError("unsupported evidence class")
    integer(freshness, "freshness", 1)
    if (
        freshness > 3600
        or not 0
        <= (stamp(as_of) - stamp(meta["observed_at"])).total_seconds()
        < freshness
    ):
        raise ValueError("stale or future telemetry")


def metric_time_ns(value):
    """Monitoring RFC3339 UTC timestamps, retaining up to nanosecond precision."""
    if type(value) is not str:
        raise ValueError("metric timestamp required")
    match = re.fullmatch(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z", value
    )
    if not match:
        raise ValueError("metric timestamp format unsupported")
    return int(stamp(match[1] + "Z").timestamp()) * 10**9 + int(
        (match[2] or "").ljust(9, "0")
    )


def snapshot(provider, raw, meta, devices, unknowns):
    result = dict(
        schema="dimaggi-telemetry/v1",
        provider=provider,
        metadata=deepcopy(meta),
        raw_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        devices=devices,
        unknowns=sorted(set(unknowns)),
        execution_authorized=False,
        mutation_request=None,
    )
    result["snapshot_digest"] = digest(result)
    return result


def nvidia_smi(raw, meta, as_of, freshness=300):
    metadata(meta, as_of, freshness)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("bounded CSV bytes required")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8")), strict=True))
    array(rows, "GPU rows")
    devices = []
    seen = set()
    unknowns = ["topology", "runtime_stack", "health", "scheduler_reservations"]

    def quantity(v, unit, dimension):
        if v in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
            return None
        return normalize_quantity(v, unit, dimension)

    for row in rows:
        if len(row) != 9:
            raise ValueError("SMI fixed query requires nine fields")
        uid, name, driver, total, used, free, power, limit, mig = [
            v.strip() for v in row
        ]
        if not re.fullmatch(r"GPU-[0-9A-Fa-f-]{8,64}", uid) or uid in seen:
            raise ValueError("invalid or duplicate GPU UUID")
        if not name or len(name) > 128 or not re.fullmatch(r"[A-Za-z0-9 ._()-]+", name):
            raise ValueError("GPU name invalid")
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", driver):
            raise ValueError("driver version missing or malformed")
        seen.add(uid)
        memory = [quantity(v, "MiB", "bytes") for v in (total, used, free)]
        if all(v is not None for v in memory) and (
            memory[0] <= 0 or memory[1] + memory[2] > memory[0]
        ):
            raise ValueError("inconsistent GPU memory")
        if mig not in {"Enabled", "Disabled", "N/A", "[N/A]", "[Not Supported]"}:
            raise ValueError("unknown MIG mode")
        # Fractional watts remain exact milliwatts; no ceil/floor is hidden.
        watts = []
        for v in (power, limit):
            if v in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
                watts.append(None)
            else:
                if len(v) > 40 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", v):
                    raise ValueError("bounded decimal power required")
                from decimal import Decimal

                n = Decimal(v)
                if not n.is_finite() or n < 0 or len(v) > 40:
                    raise ValueError("invalid power")
                a, b = n.as_integer_ratio()
                value, rem = divmod(a * 1000, b)
                if rem:
                    raise ValueError("power precision exceeds milliwatts")
                watts.append(integer(value, "power_milliwatts"))
        if None in memory or None in watts:
            unknowns.append(uid + ":missing_metrics")
        partition = "whole" if mig == "Disabled" else "mig_unresolved"
        if partition != "whole":
            unknowns.append(uid + ":mig_instance_mapping")
        devices.append(
            dict(
                id=uid,
                name=name,
                driver=driver,
                device_unit="gpu",
                partition=partition,
                memory_total_bytes=memory[0],
                memory_used_bytes=memory[1],
                memory_free_bytes=memory[2],
                power_milliwatts=watts[0],
                power_limit_milliwatts=watts[1],
                observed_at=meta["observed_at"],
            )
        )
    return snapshot(
        "nvidia", raw, meta, sorted(devices, key=lambda d: d["id"]), unknowns
    )


def google_tpu(raw, meta, identity, as_of, freshness=600):
    metadata(meta, as_of, freshness)
    obj(identity, ("project_id", "zone", "instance_id"), "Google resource identity")
    for k, v in identity.items():
        text(v, k)
    body = loads(raw.decode("utf-8"))
    if type(body) is not dict or not set(body) <= {
        "timeSeries",
        "nextPageToken",
        "executionErrors",
        "unreachable",
        "unit",
    }:
        raise ValueError("unsupported Monitoring envelope")
    if (
        body.get("nextPageToken")
        or body.get("executionErrors")
        or body.get("unreachable")
    ):
        raise ValueError("partial Monitoring result cannot be normalized")
    if body.get("unit", "By") != "By":
        raise ValueError("TPU memory requires byte units")
    values = {}
    for series in array(body.get("timeSeries"), "timeSeries"):
        if (
            type(series) is not dict
            or not {"metric", "resource", "metricKind", "valueType", "points"}
            <= set(series)
            or not set(series)
            <= {
                "metric",
                "resource",
                "metricKind",
                "valueType",
                "points",
                "unit",
                "metadata",
            }
        ):
            raise ValueError("malformed time series")
        if (
            series["metricKind"] != "GAUGE"
            or series["valueType"] != "INT64"
            or series.get("unit", "By") != "By"
        ):
            raise ValueError("unaggregated INT64 byte gauge required")
        obj(series["metric"], ("type", "labels"), "metric")
        kind = series["metric"]["type"]
        if kind not in {TPU_PREFIX + "memory_total", TPU_PREFIX + "memory_used"}:
            raise ValueError("unsupported metric")
        obj(series["metric"]["labels"], ("accelerator_id",), "metric labels")
        aid = series["metric"]["labels"]["accelerator_id"]
        text(aid, "accelerator_id")
        obj(series["resource"], ("type", "labels"), "resource")
        if (
            series["resource"]["type"] != "gce_instance"
            or series["resource"]["labels"] != identity
        ):
            raise ValueError("foreign monitored resource")
        key = (aid, kind)
        if key in values:
            raise ValueError("duplicate device metric series")
        points = array(series["points"], "points")
        seen = set()
        parsed = []
        for point in points:
            obj(point, ("interval", "value"), "point")
            interval = point["interval"]
            if (
                type(interval) is not dict
                or not {"endTime"} <= set(interval)
                or not set(interval) <= {"startTime", "endTime"}
            ):
                raise ValueError("point interval invalid")
            end = interval["endTime"]
            metric_time_ns(end)
            if interval.get("startTime", end) != end:
                raise ValueError("aggregated/noninstantaneous point unsupported")
            if metric_time_ns(end) in seen:
                raise ValueError("conflicting point timestamps")
            seen.add(metric_time_ns(end))
            obj(point["value"], ("int64Value",), "point value")
            value = point["value"]["int64Value"]
            if type(value) is not str or not re.fullmatch(r"0|[1-9][0-9]{0,15}", value):
                raise ValueError("exact nonnegative INT64 string required")
            parsed.append((end, integer(int(value), "memory_bytes")))
        end, value = max(parsed, key=lambda p: metric_time_ns(p[0]))
        if not 0 <= metric_time_ns(as_of) - metric_time_ns(
            end
        ) < freshness * 10**9 or metric_time_ns(end) > metric_time_ns(
            meta["observed_at"]
        ):
            raise ValueError("stale/future metric point")
        values[key] = (end, value)
    devices = []
    for aid in sorted({k[0] for k in values}):
        total = values.get((aid, TPU_PREFIX + "memory_total"))
        used = values.get((aid, TPU_PREFIX + "memory_used"))
        if (
            total is None
            or used is None
            or metric_time_ns(total[0]) != metric_time_ns(used[0])
        ):
            raise ValueError("paired same-timestamp memory gauges required")
        if total[1] <= 0 or used[1] > total[1]:
            raise ValueError("inconsistent TPU memory")
        devices.append(
            dict(
                id=aid,
                device_unit="runtime_device",
                partition="unresolved",
                memory_total_bytes=total[1],
                memory_used_bytes=used[1],
                memory_free_bytes=total[1] - used[1],
                observed_at=total[0],
            )
        )
    return snapshot(
        "google",
        raw,
        meta,
        devices,
        [
            "physical_chip_mapping",
            "topology",
            "runtime_stack",
            "health",
            "scheduler_reservations",
            "power",
        ],
    )


def memory_screen(
    observed,
    expected_digest,
    expected_meta,
    device_mapping,
    required_bytes,
    as_of,
    freshness=300,
):
    """Explicit join into the planner: per-device memory screen, never free slots.

    Mapping is trusted inventory, not inferred from a product name or device ID.
    It must be one-to-one; multi-core/shared HBM layouts need a separate adapter.
    """
    if type(observed) is not dict:
        raise ValueError("snapshot required")
    supplied = observed.get("snapshot_digest")
    body = {k: v for k, v in observed.items() if k != "snapshot_digest"}
    if supplied != digest(body) or supplied != sha(expected_digest):
        raise ValueError("snapshot pin mismatch")
    obj(
        observed,
        (
            "schema",
            "provider",
            "metadata",
            "raw_digest",
            "devices",
            "unknowns",
            "execution_authorized",
            "mutation_request",
            "snapshot_digest",
        ),
        "telemetry snapshot",
    )
    if (
        observed["schema"] != "dimaggi-telemetry/v1"
        or observed["provider"] not in {"nvidia", "google"}
        or observed["execution_authorized"] is not False
        or observed["mutation_request"] is not None
    ):
        raise ValueError("invalid telemetry boundary")
    sha(observed["raw_digest"])
    metadata(observed["metadata"], as_of, freshness)
    if observed["metadata"] != expected_meta:
        raise ValueError("source/epoch/target/evidence mismatch")
    integer(required_bytes, "required memory", 1)
    devices = array(observed["devices"], "devices")
    if type(device_mapping) is not dict or set(device_mapping) != {
        d["id"] for d in devices
    }:
        raise ValueError("complete explicit inventory mapping required")
    for value in device_mapping.values():
        text(value, "physical inventory identity")
    if len(set(device_mapping.values())) != len(device_mapping):
        raise ValueError("shared-device memory mapping unsupported")
    reasons = []
    seen = set()
    for d in devices:
        text(d["id"], "device id")
        if d["id"] in seen:
            raise ValueError("duplicate device identity")
        seen.add(d["id"])
        expected_unit = "gpu" if observed["provider"] == "nvidia" else "runtime_device"
        expected_partitions = (
            {"whole", "mig_unresolved"}
            if observed["provider"] == "nvidia"
            else {"unresolved"}
        )
        if (
            d["device_unit"] != expected_unit
            or d["partition"] not in expected_partitions
        ):
            raise ValueError("unsupported device semantics")
        total, used, free = (
            d[k]
            for k in ("memory_total_bytes", "memory_used_bytes", "memory_free_bytes")
        )
        for v in (total, used, free):
            if v is not None:
                integer(v, "memory")
        if total is not None and (
            total <= 0
            or (used is not None and used > total)
            or (free is not None and free > total)
            or (used is not None and free is not None and used + free > total)
        ):
            raise ValueError("inconsistent memory")
        if metric_time_ns(d["observed_at"]) > metric_time_ns(
            observed["metadata"]["observed_at"]
        ):
            raise ValueError("future metric point")
        if (
            not 0
            <= metric_time_ns(as_of) - metric_time_ns(d["observed_at"])
            < freshness * 10**9
        ):
            reasons.append(d["id"] + ":stale")
        if d.get("partition") == "mig_unresolved":
            reasons.append(d["id"] + ":MIG_mapping_unresolved")
        value = d["memory_free_bytes"]
        if value is None:
            reasons.append(d["id"] + ":memory_unknown")
        elif integer(value, "memory free") < required_bytes:
            reasons.append(d["id"] + ":memory_insufficient")
    return dict(
        schema="dimaggi-telemetry-memory-screen/v1",
        snapshot_digest=supplied,
        inventory_digest=digest(device_mapping),
        required_bytes_per_device=required_bytes,
        status="refused" if reasons else "pass",
        reasons=reasons,
        execution_authorized=False,
        mutation_request=None,
        unperformed=[
            "scheduler_reservation",
            "full_stack_compatibility",
            "topology",
            "isolation",
            "power",
            "performance",
        ],
    )
