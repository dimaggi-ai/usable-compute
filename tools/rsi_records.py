#!/usr/bin/env python3
"""Check bounded read-only RSI records; never authenticate reports or authorize expansion.

Useful credit is unique per repository, snapshot and evidence identity, regardless
of task/finding/disposition labels. Shared evidence counts once and marks the gate
with "duplicate decision content; no useful credit", even for distinct findings.
Without evidence_sha256, identity hashes the normalized reference, not an
authenticated content digest. Normalization strips surrounding whitespace, drops
empty query/fragment markers and lowercases only the URI scheme, never the path.
An optional disposition evidence_sha256 must be 64 lowercase hex characters and
replaces the reference identity; it is caller-supplied and remains unauthenticated.
Different references or dishonest digests can still evade content deduplication.
Legacy records without chronology earn zero useful credit; every effort_and_cost
value is null because chronology is missing (the summary object is retained).
"""
import argparse
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import math
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration" / "receiver" / "src"))
from dimaggi_receiver.observations import _utc

MAX_BYTES = 262144
REPOSITORIES = {"scheduler-vs-more-gpus", "span-contract"}
BINDING = {"repo", "snapshot", "profile", "objective", "denominator", "unit", "workload"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate JSON field: " + key)
        result[key] = value
    return result


def load(raw, *, root_array_limits=None, max_bytes=MAX_BYTES):
    """Load bounded JSON; trusted callers may size named top-level arrays.

    Array overrides do not apply to nested arrays or relax the depth limit.
    Ordinary records and all admission inputs keep the default 256-entry cap.
    """
    root_array_limits = root_array_limits or {}
    require(len(raw) <= max_bytes, "record exceeds byte limit")
    def invalid_constant(value):
        raise ValueError("non-finite JSON number: " + value)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
    except (UnicodeError, RecursionError) as error:
        raise ValueError("invalid UTF-8 or excessive JSON depth") from error
    def bounded(node, depth=0, array_limit=256):
        require(depth <= 16, "record exceeds depth limit")
        if isinstance(node, dict):
            for key, value in node.items():
                limit = root_array_limits.get(key, 256) if depth == 0 else 256
                bounded(value, depth + 1, limit)
        elif isinstance(node, list):
            require(len(node) <= array_limit, f"record array exceeds {array_limit} entries")
            for value in node:
                bounded(value, depth + 1)
        elif isinstance(node, str):
            require(len(node) <= 2048 and not any(0xD800 <= ord(c) <= 0xDFFF for c in node),
                    "invalid or oversized string")
        elif isinstance(node, float):
            require(math.isfinite(node), "non-finite number")
    bounded(value)
    return value


def fields(value, keys, label, optional=()):
    require(isinstance(value, dict) and set(keys) <= set(value) <= set(keys) | set(optional),
            label + ": exact fields required")


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + ": nonempty text required")


def array(value, label):
    require(isinstance(value, list) and len(value) <= 256, label + ": bounded array required")


def instant(value, label):
    text(value, label)
    require(value.endswith("Z"), label + ": UTC Z timestamp required")
    try:
        return _utc(value)
    except ValueError as error:
        raise ValueError(label + ": invalid timestamp") from error


def amount(value, label, missing):
    if value is None:
        missing.append(label)
        return None
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    require(valid, label + ": finite nonnegative number or null required")
    return Decimal(str(value))


def evidence_identity(item):
    if "evidence_sha256" in item:
        return item["evidence_sha256"]
    reference = item["evidence"].strip()
    base, fragment_marker, fragment = reference.partition("#")
    path, query_marker, query = base.partition("?")
    reference = path + (query_marker + query if query else "")
    reference += fragment_marker + fragment if fragment else ""
    reference = re.sub(r"^([A-Za-z][A-Za-z0-9+.-]*):",
                       lambda match: match.group(1).lower() + ":", reference)
    return hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _assess_records(record, now=None):
    """Assess caller-reported records against the inherited gate, not their truth."""
    now = now or datetime.now(timezone.utc)
    require(now.tzinfo is not None and now.utcoffset() == timedelta(0), "evaluation clock must be UTC")
    fields(record, {"schema", "evidence_class", "repositories", "required_checks", "tasks",
                    "weeks", "dispositions", "setup"}, "record", optional={"baseline_frozen_at"})
    baseline = record.get("baseline_frozen_at")
    baseline = instant(baseline, "baseline_frozen_at") if baseline is not None else None
    require(record["schema"] == "dimaggi-rsi-records/v1", "unsupported schema")
    require(record["evidence_class"] in ("observed", "synthetic"), "explicit evidence class required")
    repos = record["repositories"]
    array(repos, "repositories")
    require(len(repos) == 2 and all(isinstance(x, str) for x in repos) and set(repos) == REPOSITORIES,
            "scope requires exactly the two approved repositories")
    checks = record["required_checks"]
    array(checks, "required_checks")
    for check in checks:
        text(check, "required check ID")
    require(len(checks) == len(set(checks)), "duplicate required check")
    missing, failures = [], []
    if not checks:
        missing.append("required controlled acceptance checks")
    totals = {"manual_seconds": Decimal(0), "assisted_seconds": Decimal(0),
              "manual_cost": Decimal(0), "assisted_cost": Decimal(0), "setup_seconds": None,
              "setup_cost": None}
    currencies = set()

    def effort(value, label):
        fields(value, {"active_seconds", "cost_amount", "currency", "evidence"}, label)
        seconds = amount(value["active_seconds"], label + ".active_seconds", missing)
        cost = amount(value["cost_amount"], label + ".cost_amount", missing)
        currency = value["currency"]
        if currency is None:
            missing.append(label + ".currency")
        else:
            require(isinstance(currency, str) and len(currency) == 3 and currency.isascii() and currency.isalpha() and currency.isupper(), label + ": currency requires three uppercase letters")
            currencies.add(currency)
        if value["evidence"] is None:
            missing.append(label + ".evidence")
        else:
            text(value["evidence"], label + ".evidence")
        return seconds, cost

    totals["setup_seconds"], totals["setup_cost"] = effort(record["setup"], "setup")
    array(record["tasks"], "tasks")
    tasks = {}
    snapshot_times = {}
    seen_checks = set()
    for task in record["tasks"]:
        fields(task, {"id", "manual_binding", "assisted_binding", "manual", "assisted",
                      "checks", "exposure", "evidence"}, "task", optional={"snapshot_created_at"})
        identifier = task["id"]
        text(identifier, "task ID")
        require(identifier not in tasks, "duplicate task ID")
        tasks[identifier] = task
        created = task.get("snapshot_created_at")
        snapshot_times[identifier] = instant(created, "snapshot_created_at") if created is not None else None
        for key in ("manual_binding", "assisted_binding"):
            fields(task[key], BINDING, key)
            for name, value in task[key].items():
                text(value, key + "." + name)
            require(task[key]["repo"] in REPOSITORIES, "task repository outside scope")
        if task["manual_binding"] != task["assisted_binding"]:
            failures.append(identifier + ": incomparable binding/denominator")
        for key in ("exposure", "evidence"):
            if task[key] is None:
                missing.append(identifier + "." + key)
            else:
                text(task[key], identifier + "." + key)
        for mode in ("manual", "assisted"):
            seconds, cost = effort(task[mode], identifier + "." + mode)
            if seconds is not None:
                totals[mode + "_seconds"] += seconds
            if cost is not None:
                totals[mode + "_cost"] += cost
        array(task["checks"], "task checks")
        local = set()
        for check in task["checks"]:
            fields(check, {"id", "outcome", "evidence"}, "check")
            text(check["id"], "check ID")
            require(check["id"] in checks and check["id"] not in local, "unknown or duplicate task check")
            local.add(check["id"])
            seen_checks.add(check["id"])
            require(check["outcome"] in ("pass", "fail", "missing", "not_run"), "invalid check outcome")
            if check["outcome"] == "fail":
                failures.append(identifier + ": mandatory check failed: " + check["id"])
            if check["outcome"] in ("missing", "not_run") or check["evidence"] is None:
                missing.append(identifier + ": incomplete mandatory check: " + check["id"])
            if check["evidence"] is not None:
                text(check["evidence"], "check evidence")
        missing.extend(identifier + ": missing mandatory check: " + name for name in sorted(set(checks) - local))
    if not tasks:
        missing.append("matched baseline/assisted tasks")
    if {task["manual_binding"]["repo"] for task in tasks.values()} != REPOSITORIES:
        missing.append("matched tasks covering both approved repositories")
    require(len(currencies) <= 1, "cost currencies differ; conversion is outside this contract")
    array(record["weeks"], "weeks")
    require(len(record["weeks"]) <= 4, "this contract accepts at most four weekly windows")
    task_starts = {}
    weekly_ids, used_tasks, previous_end = set(), set(), None
    for week in record["weeks"]:
        fields(week, {"id", "start", "end", "recorded_at", "task_ids", "evidence"}, "week")
        text(week["id"], "week ID")
        require(week["id"] not in weekly_ids, "duplicate week ID")
        weekly_ids.add(week["id"])
        start, end, captured = (instant(week[key], "week." + key) for key in ("start", "end", "recorded_at"))
        require(end - start == timedelta(days=7), "weekly window must span seven elapsed days")
        require(previous_end is None or start >= previous_end, "weekly windows overlap or are reordered")
        require(start < end <= captured <= now, "future or incomplete observation window")
        previous_end = end
        array(week["task_ids"], "week task IDs")
        require(len(week["task_ids"]) == len(set(week["task_ids"])) and all(x in tasks for x in week["task_ids"]), "unknown or duplicate weekly task")
        require(not used_tasks.intersection(week["task_ids"]), "one task cannot be counted in multiple weeks")
        used_tasks.update(week["task_ids"])
        task_starts.update((identifier, start) for identifier in week["task_ids"])
        if not week["task_ids"]:
            missing.append(week["id"] + ": no actual tasks")
        if week["evidence"] is None:
            missing.append(week["id"] + ".evidence")
        else:
            text(week["evidence"], "week evidence")
    if len(weekly_ids) != 4:
        missing.append("four genuine weekly observations")
    if set(tasks) - used_tasks:
        missing.append("tasks not bound to an observed weekly window")
    array(record["dispositions"], "dispositions")
    disposition_ids, findings, useful = set(), set(), 0
    credited_content = set()
    for item in record["dispositions"]:
        fields(item, {"id", "finding_id", "task_id", "kind", "prior_known", "owner", "accepted_at", "evidence"}, "disposition", optional={"evidence_sha256"})
        if "evidence_sha256" in item:
            digest = item["evidence_sha256"]
            require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                    "disposition.evidence_sha256: 64 lowercase hex characters required")
        for key in ("id", "finding_id", "task_id"):
            text(item[key], "disposition." + key)
        require(item["id"] not in disposition_ids and item["finding_id"] not in findings, "duplicate disposition or finding credit")
        disposition_ids.add(item["id"])
        findings.add(item["finding_id"])
        require(item["task_id"] in tasks, "unknown disposition task")
        require(item["kind"] in ("useful_change", "justified_no_change", "rejected", "incomplete"), "unsupported disposition kind")
        require(type(item["prior_known"]) is bool, "prior_known must be boolean")
        complete = True
        for key in ("owner", "accepted_at", "evidence"):
            if item[key] is None:
                missing.append(item["id"] + "." + key)
                complete = False
            else:
                text(item[key], "disposition." + key)
        if item["accepted_at"] is not None:
            require(instant(item["accepted_at"], "accepted_at") <= now, "future disposition")
        if item["kind"] in ("useful_change", "justified_no_change") and not item["prior_known"] and complete:
            accepted = instant(item["accepted_at"], "accepted_at")
            eligible = True
            for label, boundary in (("baseline freeze", baseline),
                                    ("task window start", task_starts.get(item["task_id"])),
                                    ("snapshot creation", snapshot_times[item["task_id"]])):
                if boundary is None:
                    missing.append(item["id"] + ": " + label + " not established; no useful credit")
                    eligible = False
                elif accepted < boundary:
                    failures.append(item["id"] + ": accepted before " + label + "; no useful credit")
                    eligible = False
            # Caller-supplied identity only; no evidence is fetched or authenticated.
            # Task/finding/disposition labels cannot mint another useful credit.
            binding = tasks[item["task_id"]]["assisted_binding"]
            identity = (binding["repo"], binding["snapshot"], evidence_identity(item))
            if identity in credited_content:
                failures.append(item["id"] + ": duplicate decision content; no useful credit")
                eligible = False
            if eligible:
                credited_content.add(identity)
                useful += 1
    if useful < 2:
        failures.append("fewer than two unique evidence-linked useful dispositions")
    if totals["assisted_seconds"] > totals["manual_seconds"]:
        failures.append("comparable ongoing review effort increased")
    gate = "insufficient_evidence" if missing else "not_met" if failures else "recorded_criteria_met"
    require(all(value is None or math.isfinite(float(value)) for value in totals.values()), "aggregate measurement exceeds finite output range")
    summary = {key: float(value) if value is not None else None for key, value in totals.items()}
    summary["assisted_seconds_including_setup"] = (float(totals["assisted_seconds"] + totals["setup_seconds"]) if totals["setup_seconds"] is not None else None)
    summary["assisted_cost_including_setup"] = (float(totals["assisted_cost"] + totals["setup_cost"]) if totals["setup_cost"] is not None else None)
    require(all(value is None or math.isfinite(value) for value in summary.values()), "aggregate including setup exceeds finite output range")
    if missing:
        # Partial sums must not masquerade as totals when underlying measurements are missing.
        for key in summary:
            summary[key] = None
    return {"schema": "dimaggi-rsi-record-assessment/v1", "evidence_class": record["evidence_class"],
            "assessment": "synthetic_only" if record["evidence_class"] == "synthetic" else gate,
            "recorded_gate": gate, "useful_dispositions": useful, "weekly_observations": len(weekly_ids),
            "missing": missing, "gate_failures": failures, "effort_and_cost": summary,
            "currency": next(iter(currencies), None), "authenticated": False,
            "human_reports_verified": False, "continuation_authorized": False,
            "candidate_execution_authorized": False,
            "scope": "preliminary caller-record consistency only; no authenticity, real-world truth or expansion approval"}


def assess(record, now=None, *, artifacts=None, replay_store=None):
    """Assess records only after admitting supplied evidence and consuming its identity."""
    from rsi_admission import admit_supplied_batch, claim_designated
    result = _assess_records(record, now)
    require(replay_store is None, 'caller replay store refused')
    if not artifacts:
        result['missing'].append('admitted evidence and persistent replay store required')
        result['recorded_gate'] = 'insufficient_evidence'
        result['useful_dispositions'] = 0
        if record['evidence_class'] != 'synthetic': result['assessment'] = 'insufficient_evidence'
        return result
    tasks = {task['id']: task for task in record['tasks']}
    batches = []
    for item in record['dispositions']:
        if item['kind'] not in ('useful_change', 'justified_no_change') or item['prior_known']:
            continue
        supplied = artifacts.get(item['evidence'])
        require(isinstance(supplied, dict) and set(supplied) == {'manifest','payload','trust'}, 'admission evidence required')
        admitted = admit_supplied_batch(supplied['manifest'], supplied['payload'], supplied['trust'])
        require(admitted['admission'] == 'accepted_static', 'records admission refused')
        manifest = load(supplied['manifest'])
        require(record['evidence_class'] == manifest['evidence_class'], 'admission evidence class mismatch')
        scope = manifest['scope']; binding = tasks[item['task_id']]['assisted_binding']
        for key, target in [('repo','repository'), ('snapshot','snapshot'), ('profile','profile_id'),
                            ('objective','objective'), ('denominator','denominator'), ('unit','unit'), ('workload','workload')]:
            require(binding[key] == scope[target], 'admission scope mismatch: ' + key)
        if 'evidence_sha256' in item:
            require(item['evidence_sha256'] == admitted['payload_sha256'], 'admission evidence digest mismatch')
        batches.append((supplied['manifest'], supplied['payload']))
    if result['recorded_gate'] == 'recorded_criteria_met':
        claim_designated(batches)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        with args.input.open("rb") as stream:
            record = load(stream.read(MAX_BYTES + 1))
        result = assess(record)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    except (ValueError, OSError, TypeError, OverflowError) as error:
        print(json.dumps({"error": str(error), "continuation_authorized": False}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
