"""Explicit telemetry and approved-source operations for the installed receiver."""

from datetime import datetime, timezone
from .jsonio import loads, read_file
from . import telemetry, telemetry_collect, source_watch

COMMANDS = {
    "telemetry-import",
    "telemetry-memory-screen",
    "telemetry-collect-nvidia",
    "telemetry-collect-google",
    "source-collect",
    "source-compare",
}


def add_commands(commands):
    for name in sorted(COMMANDS):
        p = commands.add_parser(name)
        if name.startswith("source-"):
            p.add_argument("--config", required=True)
            if name == "source-compare":
                p.add_argument("--baseline", required=True)
                p.add_argument("--current", required=True)
        elif name == "telemetry-memory-screen":
            for arg in ("input", "snapshot-digest", "metadata", "mapping", "as-of"):
                p.add_argument("--" + arg, required=True)
            p.add_argument("--required-bytes", type=int, required=True)
            p.add_argument("--freshness", type=int, default=300)
        else:
            p.add_argument("--metadata", required=True)
            p.add_argument("--freshness", type=int, default=300)
            if name == "telemetry-import":
                p.add_argument(
                    "--provider", choices=["nvidia", "google"], required=True
                )
                p.add_argument("--input", required=True)
                p.add_argument("--identity")
                p.add_argument("--as-of", required=True)
            elif name == "telemetry-collect-nvidia":
                p.add_argument("--binary", required=True)
                p.add_argument("--binary-digest", required=True)
            else:
                p.add_argument("--identity", required=True)
                p.add_argument("--token-file", required=True)
                p.add_argument("--start-time", required=True)


def run(a):
    def read(p):
        return loads(read_file(p).decode("utf-8"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if a.command == "source-collect":
        return source_watch.collect(read(a.config), now)
    if a.command == "source-compare":
        return source_watch.compare(read(a.config), read(a.baseline), read(a.current))
    meta = read(a.metadata)
    if a.command == "telemetry-memory-screen":
        return telemetry.memory_screen(
            read(a.input),
            a.snapshot_digest,
            meta,
            read(a.mapping),
            a.required_bytes,
            a.as_of,
            a.freshness,
        )
    if a.command == "telemetry-import":
        raw = read_file(a.input)
        if a.provider == "nvidia":
            return telemetry.nvidia_smi(raw, meta, a.as_of, a.freshness)
        if not a.identity:
            raise ValueError("Google resource identity required")
        return telemetry.google_tpu(raw, meta, read(a.identity), a.as_of, a.freshness)
    if meta.get("evidence_class") != "hardware_observed":
        raise ValueError(
            "live collector requires explicit hardware-observed source configuration"
        )
    meta["observed_at"] = now
    if a.command == "telemetry-collect-nvidia":
        return telemetry_collect.collect_nvidia(
            a.binary, a.binary_digest, meta, now, a.freshness
        )
    return telemetry_collect.collect_google(
        read(a.identity),
        telemetry_collect.read_token(a.token_file),
        meta,
        now,
        a.start_time,
        a.freshness,
    )
