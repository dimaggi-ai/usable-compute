"""Offline attributed read projections; no scheduler or authority write path.

The caller configures each source and its ordering contract. ``source_sequence``
is an adapter-supplied monotonic integer within one source epoch and record; an
opaque scheduler resourceVersion must never be guessed to have this meaning.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any


KINDS = {"permission", "attempt", "workload"}
STATES = {
    "permission": {"allowed", "denied", "unresolved", "expired"},
    "attempt": {"not_started", "submitted", "completed", "failed", "unknown"},
    "workload": {"pending", "running", "succeeded", "failed", "absent", "deleted", "unknown"},
}
INTENT_KEYS = {
    "request_id", "report_id", "profile_id", "target_id", "workload_id",
    "desired_state", "sources", "evidence_class",
}
EVENT_KEYS = {
    "event_id", "kind", "source_id", "source_epoch", "source_record_id",
    "source_version", "source_sequence", "observed_at_utc", "request_id",
    "report_id", "profile_id", "target_id", "workload_id", "object_id",
    "state", "evidence_class", "payload",
}
IDENTITIES = ("request_id", "report_id", "profile_id", "target_id", "workload_id")


class ObservationError(ValueError):
    """Invalid or incorrectly bound input; it supplies no usable observation."""


class SourceConflict(ObservationError):
    """An immutable identity/version was reused with different content."""


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ObservationError(f"{field} must be a nonempty trimmed string")
    return value


def _utc(value: Any) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value
    ):
        raise ObservationError("timestamps require UTC Z and at most microsecond precision")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ObservationError("invalid UTC timestamp") from exc


def _json(value: Any) -> str:
    # Reject non-JSON Python structures instead of silently coercing tuple/keys.
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ObservationError("payload must contain only finite JSON values")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _seconds(delta: Any) -> Decimal:
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 1000000


class ObservationStore:
    """Local durable journal, not an authenticated collector or executor.

    Source events and workload intents are immutable. ``reconcile`` stores an
    application-owned snapshot/cases; it does not alter imported authority state.
    Closing/reopening the same SQLite file preserves evidence and idempotency.
    """

    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS intents (
                request_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                position INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL, request_id TEXT NOT NULL,
                source_id TEXT NOT NULL, epoch TEXT NOT NULL, record_id TEXT NOT NULL,
                version TEXT NOT NULL, sequence INTEGER NOT NULL,
                recorded_at TEXT NOT NULL, body TEXT NOT NULL,
                UNIQUE(source_id, epoch, record_id, version),
                FOREIGN KEY(request_id) REFERENCES intents(request_id),
                FOREIGN KEY(source_id) REFERENCES sources(source_id));
            CREATE TABLE IF NOT EXISTS conflicts (
                conflict_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL, reason TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reconciliations (
                reconciliation_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
                as_of TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS triage (
                triage_id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL, body TEXT NOT NULL);
        """)
        for table in ("intents", "sources", "events", "conflicts", "reconciliations", "triage"):
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                    BEFORE {operation} ON {table}
                    BEGIN SELECT RAISE(ABORT, 'append-only journal'); END""")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ObservationStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def register_source(self, source_id: str, kind: str, target_id: str) -> bool:
        """Declare a read-source role; registration authenticates no evidence."""
        _identifier(source_id, "source_id")
        _identifier(target_id, "target_id")
        if type(kind) is not str or kind not in KINDS:
            raise ObservationError("unknown source kind")
        existing = self.db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
        if existing:
            if existing["kind"] != kind or existing["target_id"] != target_id:
                raise SourceConflict("source identity already has a different role/target")
            return False
        with self.db:
            self.db.execute("INSERT INTO sources VALUES (?, ?, ?)", (source_id, kind, target_id))
        return True

    def register_intent(self, intent: dict[str, Any]) -> bool:
        """Persist application intent before importing any attempt/observation."""
        if type(intent) is not dict or set(intent) != INTENT_KEYS:
            raise ObservationError("intent fields must match the declared contract")
        for field in IDENTITIES:
            _identifier(intent[field], field)
        if type(intent["desired_state"]) is not str or intent["desired_state"] not in {"present", "succeeded"}:
            raise ObservationError("desired_state must be present or succeeded")
        if type(intent["evidence_class"]) is not str or intent["evidence_class"] not in {"synthetic", "observed"}:
            raise ObservationError("intent evidence_class must be synthetic or observed")
        if type(intent["sources"]) is not dict or set(intent["sources"]) != KINDS:
            raise ObservationError("intent must declare all three read-source roles")
        for kind, source_id in intent["sources"].items():
            _identifier(source_id, "source_id")
            source = self.db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            if not source or source["kind"] != kind or source["target_id"] != intent["target_id"]:
                raise ObservationError("intent source role/target is unregistered or mismatched")
        body = _json(intent)
        existing = self.db.execute("SELECT body FROM intents WHERE request_id=?", (intent["request_id"],)).fetchone()
        if existing:
            if existing["body"] != body:
                raise SourceConflict("request identity reused with different intent")
            return False
        with self.db:
            self.db.execute("INSERT INTO intents VALUES (?, ?)", (intent["request_id"], body))
        return True

    def intent(self, request_id: str) -> dict[str, Any]:
        _identifier(request_id, "request_id")
        row = self.db.execute("SELECT body FROM intents WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            raise ObservationError("unknown request identity")
        return json.loads(row["body"])

    def _conflict(self, event: dict[str, Any], recorded_at: str, reason: str) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO conflicts VALUES (?, ?, ?, ?, ?)", (
                _digest([event, reason]), event["request_id"], recorded_at, reason, _json(event)))
        raise SourceConflict(reason)

    def append(self, event: dict[str, Any], *, recorded_at_utc: str) -> bool:
        """Append evidence; exact retries are no-ops, changed reuse is a conflict.

        A reset epoch is retained, but cannot supersede an earlier epoch without
        reconciliation. Events may arrive late; source sequence, never arrival
        order, chooses the latest event within a declared epoch/record.
        """
        if type(event) is not dict or set(event) != EVENT_KEYS:
            raise ObservationError("event fields must match the declared contract")
        body = _json(event)
        for field in (*IDENTITIES, "event_id", "source_id", "source_epoch", "source_record_id", "source_version"):
            _identifier(event[field], field)
        intent = self.intent(event["request_id"])
        if any(event[field] != intent[field] for field in IDENTITIES):
            raise ObservationError("event identity/target/profile/report does not match intent")
        kind = event["kind"]
        if type(kind) is not str or kind not in KINDS or event["source_id"] != intent["sources"][kind]:
            raise ObservationError("event source is not the configured owner for this kind")
        if type(event["state"]) is not str or event["state"] not in STATES[kind]:
            raise ObservationError("unsupported source state")
        if event["evidence_class"] != intent["evidence_class"]:
            raise ObservationError("synthetic and observed evidence cannot share an intent")
        if type(event["source_sequence"]) is not int or not 0 <= event["source_sequence"] <= 2**63 - 1:
            raise ObservationError("source_sequence must be a nonnegative SQLite-range integer")
        observed = _utc(event["observed_at_utc"])
        if _utc(recorded_at_utc) < observed:
            raise ObservationError("observation is later than its recorded time")
        if event["object_id"] is not None:
            _identifier(event["object_id"], "object_id")
        if type(event["payload"]) is not dict:
            raise ObservationError("payload must be an object")
        if "foreign_change" in event["payload"] and type(event["payload"]["foreign_change"]) is not bool:
            raise ObservationError("foreign_change must be an explicit boolean assertion")
        if kind == "permission" and (event["source_record_id"] != event["request_id"] or event["object_id"] is not None):
            raise ObservationError("permission must reference request identity, not workload object")
        if kind == "workload" and event["source_record_id"] != event["workload_id"]:
            raise ObservationError("workload record must reference the declared workload identity")
        if kind == "workload" and event["state"] not in {"absent", "unknown"} and event["object_id"] is None:
            raise ObservationError("a materialized workload observation requires immutable object identity")
        if kind == "attempt" and event["state"] in {"submitted", "completed"} and event["object_id"] is None:
            raise ObservationError("submission acknowledgement requires attributed object identity")
        stream = self.db.execute("""SELECT body FROM events WHERE source_id=? AND epoch=?
            AND record_id=? LIMIT 1""", (event["source_id"], event["source_epoch"], event["source_record_id"])).fetchone()
        if stream:
            previous = json.loads(stream["body"])
            if any(previous[field] != event[field] for field in IDENTITIES):
                self._conflict(event, recorded_at_utc, "source record stream rebound to a different request/binding")
        if event["object_id"] is not None:
            for other in self.db.execute("SELECT body FROM events WHERE request_id != ?", (event["request_id"],)):
                previous = json.loads(other["body"])
                if previous["target_id"] == event["target_id"] and previous["object_id"] == event["object_id"]:
                    self._conflict(event, recorded_at_utc, "immutable target object already attributed to another request")
        existing = self.db.execute("SELECT body FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
        if existing:
            if existing["body"] != body:
                self._conflict(event, recorded_at_utc, "event identity reused with different content")
            return False
        version = self.db.execute("""SELECT body FROM events WHERE source_id=? AND epoch=?
            AND record_id=? AND version=?""", (event["source_id"], event["source_epoch"], event["source_record_id"], event["source_version"])).fetchone()
        if version:
            prior = json.loads(version["body"])
            if {k: v for k, v in prior.items() if k != "event_id"} == {k: v for k, v in event.items() if k != "event_id"}:
                return False
            self._conflict(event, recorded_at_utc, "source record/version reused with different content")
        sequence = self.db.execute("""SELECT event_id FROM events WHERE source_id=? AND epoch=?
            AND record_id=? AND sequence=?""", (event["source_id"], event["source_epoch"], event["source_record_id"], event["source_sequence"])).fetchone()
        if sequence:
            self._conflict(event, recorded_at_utc, "source sequence reused with a different version")
        with self.db:
            self.db.execute("""INSERT INTO events
                (event_id,request_id,source_id,epoch,record_id,version,sequence,recorded_at,body)
                VALUES (?,?,?,?,?,?,?,?,?)""", (
                event["event_id"], event["request_id"], event["source_id"], event["source_epoch"],
                event["source_record_id"], event["source_version"], event["source_sequence"], recorded_at_utc, body))
        return True

    def history(self, request_id: str) -> list[dict[str, Any]]:
        self.intent(request_id)
        return [{"event": json.loads(row["body"]), "recorded_at_utc": row["recorded_at"]}
                for row in self.db.execute("SELECT * FROM events WHERE request_id=? ORDER BY position", (request_id,))]

    def project(self, request_id: str, *, as_of_utc: str,
                freshness_seconds: dict[str, int | float]) -> dict[str, Any]:
        """Compute independent read projections and application divergence cases.

        No TTL defaults. Freshness expires at equality. As-of restricts both
        ingestion and source observation times, so a historical view cannot use
        evidence learned later. Case IDs are stable for the same reason/events.
        """
        intent = self.intent(request_id)
        as_of = _utc(as_of_utc)
        if type(freshness_seconds) is not dict or set(freshness_seconds) != KINDS:
            raise ObservationError("freshness_seconds requires all three source kinds")
        for value in freshness_seconds.values():
            if type(value) not in (int, float) or value <= 0 or (type(value) is float and not math.isfinite(value)):
                raise ObservationError("freshness must be explicit positive finite seconds")
        # Validate serialization as well as arithmetic. Python may refuse integer
        # encodings beyond its configured digit limit; surface a contract error.
        try:
            _json(freshness_seconds)
        except (ValueError, OverflowError) as exc:
            raise ObservationError("freshness cannot be represented as finite JSON") from exc
        history = [item for item in self.history(request_id) if _utc(item["recorded_at_utc"]) <= as_of]
        projections: dict[str, Any] = {}
        issues: list[tuple[str, list[str], str]] = []
        for kind in sorted(KINDS):
            events = [item["event"] for item in history if item["event"]["kind"] == kind]
            late: list[str] = []
            streams: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for event in events:
                key = (event["source_epoch"], event["source_record_id"])
                if key in streams and event["source_sequence"] < max(x["source_sequence"] for x in streams[key]):
                    late.append(event["event_id"])
                streams.setdefault(key, []).append(event)
            latest = []
            for group in streams.values():
                event = max(group, key=lambda x: x["source_sequence"])
                age = _seconds(as_of - _utc(event["observed_at_utc"]))
                ttl = Decimal(freshness_seconds[kind]) if type(freshness_seconds[kind]) is int else Decimal(str(freshness_seconds[kind]))
                freshness = "stale" if age >= ttl else "current"
                latest.append({"event": event, "freshness": freshness, "age_seconds": float(age)})
            latest.sort(key=lambda item: (item["event"]["source_epoch"], item["event"]["source_record_id"]))
            epochs = sorted({event["source_epoch"] for event in events})
            status = "missing" if not latest else "source_reset" if len(epochs) > 1 else "stale" if any(item["freshness"] == "stale" for item in latest) else "current"
            projections[kind] = {"status": status, "source_id": intent["sources"][kind], "source_epochs": epochs,
                                 "latest_records": latest, "late_event_ids": late}
            if status != "current":
                issues.append((f"{kind}_{status}", [item["event"]["event_id"] for item in latest], "escalate" if status == "source_reset" else "hold"))
        conflicts = [dict(row) for row in self.db.execute("SELECT * FROM conflicts WHERE request_id=? ORDER BY recorded_at, conflict_id", (request_id,)) if _utc(row["recorded_at"]) <= as_of]
        for conflict in conflicts:
            issues.append(("source_conflict", [conflict["conflict_id"]], "escalate"))
        all_events = [item["event"] for item in history]
        acknowledged = [event for event in all_events if event["kind"] == "attempt" and event["state"] in {"submitted", "completed"}]
        accepted_ids = {event["object_id"] for event in acknowledged}
        workload_events = [event for event in all_events if event["kind"] == "workload"]
        seen_ids = {event["object_id"] for event in workload_events if event["object_id"] is not None}
        if len(accepted_ids | seen_ids) > 1:
            issues.append(("object_identity_reuse_or_conflict", [event["event_id"] for event in acknowledged + workload_events if event["object_id"] is not None], "escalate"))
        current = {kind: [item["event"] for item in projection["latest_records"]]
                   for kind, projection in projections.items()}
        attempts = current["attempt"]
        workloads = current["workload"]
        for event in attempts:
            if event["state"] == "unknown":
                issues.append(("attempt_unknown", [event["event_id"]], "escalate"))
        for event in workloads:
            if event["state"] in {"absent", "deleted"}:
                issues.append(("desired_observed_divergence", [event["event_id"]], "hold"))
            if event["state"] == "unknown":
                issues.append(("workload_unknown", [event["event_id"]], "hold"))
            if event["state"] == "failed" and intent["desired_state"] == "succeeded":
                issues.append(("desired_outcome_not_met", [event["event_id"]], "hold"))
            if event["object_id"] is not None and event["object_id"] not in accepted_ids:
                issues.append(("object_not_attributed_to_submission", [event["event_id"]], "escalate"))
            if event["payload"].get("foreign_change") is True:
                issues.append(("foreign_change", [event["event_id"]], "escalate"))
        for event in current["permission"]:
            if event["state"] != "allowed":
                issues.append((f"permission_{event['state']}", [event["event_id"]], "hold"))
        cases = []
        recorded_times = {item["event"]["event_id"]: item["recorded_at_utc"] for item in history}
        recorded_times.update({item["conflict_id"]: item["recorded_at"] for item in conflicts})
        for reason, refs, disposition in issues:
            refs = sorted(set(refs))
            first_seen = max((recorded_times[ref] for ref in refs), key=_utc, default=None)
            if reason.endswith("_stale"):
                kind = reason.removesuffix("_stale")
                ttl = Decimal(freshness_seconds[kind]) if type(freshness_seconds[kind]) is int else Decimal(str(freshness_seconds[kind]))
                expiry_microseconds = int((ttl * 1000000).to_integral_value(rounding=ROUND_CEILING))
                # At least one cited latest record must have actually expired.
                # A record learned after expiry is only supportable at ingestion.
                supports = [max(_utc(recorded_times[item["event"]["event_id"]]),
                                _utc(item["event"]["observed_at_utc"]) + timedelta(microseconds=expiry_microseconds))
                            for item in projections[kind]["latest_records"] if item["freshness"] == "stale"]
                first_seen = max(_utc(first_seen), min(supports)).isoformat(timespec="microseconds").replace("+00:00", "Z")
            case_id = _digest([request_id, reason, refs])
            notes = [note for note in self.triage_history(case_id) if _utc(note["recorded_at_utc"]) <= as_of]
            cases.append({"case_id": case_id, "reason": reason,
                          "evidence_refs": refs, "first_supported_at_utc": first_seen,
                          "age_seconds": None if first_seen is None else float(_seconds(as_of - _utc(first_seen))),
                          "triage": "recorded" if notes else "untriaged", "triage_events": notes,
                          "disposition": disposition})
        # A valid acknowledgement and a failed workload have different meanings;
        # no contradictory-authority case is inferred from those two states.
        return {
            "schema_version": "dimaggi-observation-projection/v1", "intent": intent,
            "as_of_utc": as_of_utc, "freshness_seconds": freshness_seconds,
            "projections": projections, "cases": cases,
            "recommendation": "escalate" if any(c["disposition"] == "escalate" for c in cases) else "hold" if cases else "retain",
            "mutation_request": None, "automatic_resubmission": False,
            "proof_level": "synthetic_projection" if intent["evidence_class"] == "synthetic" else "unverified_imported_observation",
            "execution_proven": False,
        }

    def reconcile(self, request_id: str, *, as_of_utc: str,
                  freshness_seconds: dict[str, int | float]) -> dict[str, Any]:
        """Persist a repeatable application-owned reconciliation snapshot."""
        result = self.project(request_id, as_of_utc=as_of_utc, freshness_seconds=freshness_seconds)
        identity = _digest(result)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO reconciliations VALUES (?, ?, ?, ?)",
                            (identity, request_id, as_of_utc, _json(result)))
        return {"reconciliation_id": identity, **result}

    def reconciliation(self, reconciliation_id: str) -> dict[str, Any]:
        _identifier(reconciliation_id, "reconciliation_id")
        row = self.db.execute("SELECT body FROM reconciliations WHERE reconciliation_id=?", (reconciliation_id,)).fetchone()
        if not row:
            raise ObservationError("unknown reconciliation identity")
        return {"reconciliation_id": reconciliation_id, **json.loads(row["body"])}

    def record_triage(self, *, triage_id: str, reconciliation_id: str, case_id: str,
                      actor_id: str, disposition: str, reason: str,
                      recorded_at_utc: str) -> bool:
        """Append application triage without clearing evidence or granting action.

        Only hold/escalate/proposal are supported. A proposal is a reviewer note,
        never a mutation request. This bounded journal has no close/override path
        for source conflicts/resets or uncertainty about effects.
        """
        for field, value in (("triage_id", triage_id), ("case_id", case_id),
                             ("actor_id", actor_id), ("reason", reason)):
            _identifier(value, field)
        if type(disposition) is not str or disposition not in {"hold", "escalate", "proposal"}:
            raise ObservationError("triage disposition must be hold, escalate or proposal")
        snapshot = self.reconciliation(reconciliation_id)
        if not any(case["case_id"] == case_id for case in snapshot["cases"]):
            raise ObservationError("case is not present in the referenced reconciliation")
        if _utc(recorded_at_utc) < _utc(snapshot["as_of_utc"]):
            raise ObservationError("triage cannot precede the referenced reconciliation")
        note = {"triage_id": triage_id, "reconciliation_id": reconciliation_id,
                "case_id": case_id, "actor_id": actor_id, "disposition": disposition,
                "reason": reason, "recorded_at_utc": recorded_at_utc,
                "mutation_request": None, "grants_permission": False}
        body = _json(note)
        existing = self.db.execute("SELECT body FROM triage WHERE triage_id=?", (triage_id,)).fetchone()
        if existing:
            if existing["body"] != body:
                raise SourceConflict("triage identity reused with different content")
            return False
        with self.db:
            self.db.execute("INSERT INTO triage VALUES (?, ?, ?, ?)",
                            (triage_id, case_id, recorded_at_utc, body))
        return True

    def triage_history(self, case_id: str) -> list[dict[str, Any]]:
        _identifier(case_id, "case_id")
        notes = [json.loads(row["body"]) for row in self.db.execute("SELECT body FROM triage WHERE case_id=?", (case_id,))]
        return sorted(notes, key=lambda item: (_utc(item["recorded_at_utc"]), item["triage_id"]))
