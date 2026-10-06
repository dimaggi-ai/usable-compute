"""Internal synthetic supplied-byte admission. No artifact code or reference is run.

The caller must supply independently frozen trust bytes; hashes do not authenticate
that caller. Only the illustrative resource-count profile is implemented. Records
v1 consumption requires admission and an owner-provisioned persistent replay store.
"""
import hashlib
import json
import re
from rsi_records import load, MAX_BYTES, REPOSITORIES


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


RESOURCE_PROFILE = {
    "id": "synthetic-resource-accounting", "version": 1,
    "scope": "Illustrative resource counts at one boundary and instant; no scheduler/span physics claim",
    "fields": {"available": "count", "allocated": "count", "idle": "count"},
    "domain": "nonnegative_integer", "equation": "available=allocated+idle", "tolerance": 0,
}
SCOPE_FIELDS = {"repository", "snapshot", "profile_id", "profile_version", "profile_sha256",
                "objective", "workload", "denominator", "unit", "scenario_family", "boundary", "window"}
PROVENANCE_FIELDS = {"generator_id", "generator_version", "generator_sha256", "configuration_sha256",
                     "inputs_sha256", "seed", "approved_scope_ref"}
MEMBERSHIP = {"row_id", "source_id", "family", "group", "seed", "exposure"}


class Refusal(ValueError):
    def __init__(self, code, row=None, status="rejected"):
        self.code, self.row, self.status = code, row, status
        super().__init__(code)


def check(ok, code, row=None):
    if not ok:
        raise Refusal(code, row)


def exact(obj, fields, code, row=None):
    check(type(obj) is dict and set(obj) == set(fields), code, row)


def string(value, code):
    check(type(value) is str and bool(value.strip()), code)


def digest(value, code):
    check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, code)


def integer(value, code, row=None):
    check(type(value) is int and 0 <= value <= 2**63 - 1, code, row)


def same(a, b):
    # JSON equality is type-sensitive (Python bool and int equality is not).
    return encode(a) == encode(b)


def admit_supplied_batch(manifest_bytes, payload_bytes, trusted_profile):
    """Validate bounded supplied bytes against trusted frozen bytes, without I/O.

    Missing external authority is incomplete. A proven defect rejects the entire
    batch. Successful output is synthetic_only and grants no execution authority.
    The trust bundle is synthetic only; observed-data profiles require review.
    """
    result = {"schema": "dimaggi-rsi-artifact-admission/v1", "admission": "rejected",
              "assessment": "synthetic_only", "generator_execution_authorized": False,
              "candidate_execution_authorized": False, "continuation_authorized": False,
              "human_reports_verified": False, "checks": [], "failing_row_ids": []}
    try:
        check(type(manifest_bytes) is bytes and type(payload_bytes) is bytes, "supplied_bytes_required")
        result.update(manifest_sha256=sha(manifest_bytes), payload_sha256=sha(payload_bytes))
        m, payload = load(manifest_bytes), load(payload_bytes)
        exact(m, {"schema", "artifact_id", "evidence_class", "payload_sha256", "payload_bytes", "row_count",
                  "scope", "provenance", "partition", "invariant_profile"}, "manifest_fields")
        check(m["schema"] == "dimaggi-rsi-artifact-manifest/v1", "manifest_version")
        string(m["artifact_id"], "artifact_id")
        check(m["evidence_class"] == "synthetic", "synthetic_scope_only")
        digest(m["payload_sha256"], "payload_digest_format")
        integer(m["payload_bytes"], "payload_size_type")
        integer(m["row_count"], "row_count_type")
        check(m["payload_sha256"] == sha(payload_bytes) and m["payload_bytes"] == len(payload_bytes), "payload_binding")
        exact(payload, {"schema", "rows"}, "payload_fields")
        check(payload["schema"] == "dimaggi-rsi-artifact-payload/v1", "payload_version")
        rows = payload["rows"]
        check(type(rows) is list and 0 < len(rows) <= 256 and len(rows) == m["row_count"], "row_count")
        exact(m["scope"], SCOPE_FIELDS, "scope_fields")
        exact(m["provenance"], PROVENANCE_FIELDS, "provenance_fields")
        exact(m["partition"], {"plan_sha256", "partition_id", "row_ids"}, "partition_fields")
        exact(m["invariant_profile"], {"id", "version", "sha256"}, "invariant_profile_fields")
        result["checks"].append({"id": "content_binding", "status": "passed"})
        if trusted_profile is None:
            raise Refusal("missing_trusted_evidence", status="incomplete")
        check(type(trusted_profile) is bytes, "frozen_trust_bytes_required")
        result["trusted_sha256"] = sha(trusted_profile)
        trust = load(trusted_profile)
        exact(trust, {"schema", "evidence_class", "scope", "provenance", "partition_plan", "invariant_profile"}, "trust_fields")
        check(trust["schema"] == "dimaggi-rsi-artifact-trust/v1" and trust["evidence_class"] == "synthetic", "trust_scope")
        for key in ("scope", "provenance", "partition_plan", "invariant_profile"):
            if trust[key] is None:
                raise Refusal("missing_trusted_" + key, status="incomplete")
        scope, provenance, plan, profile = (trust[k] for k in ("scope", "provenance", "partition_plan", "invariant_profile"))
        exact(scope, SCOPE_FIELDS, "trusted_scope_fields")
        for key in SCOPE_FIELDS - {"profile_version"}:
            string(scope[key], "trusted_scope_text")
        integer(scope["profile_version"], "trusted_profile_version")
        digest(scope["profile_sha256"], "trusted_profile_digest")
        check(scope["repository"] in REPOSITORIES and scope["unit"] == "count", "repository_or_unit_scope")
        check(same(m["scope"], scope), "scope_mismatch")
        exact(provenance, PROVENANCE_FIELDS, "trusted_provenance_fields")
        for key in ("generator_id", "generator_version", "approved_scope_ref"):
            if provenance[key] is None:
                raise Refusal("missing_" + key, status="incomplete")
            string(provenance[key], "provenance_text")
        for key in ("generator_sha256", "configuration_sha256"):
            digest(provenance[key], "provenance_digest")
        check(type(provenance["inputs_sha256"]) is list and bool(provenance["inputs_sha256"]), "inputs_required")
        for value in provenance["inputs_sha256"]:
            digest(value, "input_digest")
        integer(provenance["seed"], "seed")
        check(same(m["provenance"], provenance), "provenance_mismatch")
        check(same(profile, RESOURCE_PROFILE), "unsupported_or_modified_invariant_profile")
        profile_ref = {"id": profile["id"], "version": profile["version"], "sha256": sha(encode(profile))}
        check(same(m["invariant_profile"], profile_ref), "invariant_profile_mismatch")
        result["profile_sha256"] = profile_ref["sha256"]
        exact(plan, {"schema", "partitions"}, "plan_fields")
        check(plan["schema"] == "dimaggi-rsi-partition-plan/v1", "plan_version")
        partitions = plan["partitions"]
        check(type(partitions) is list and 0 < len(partitions) <= 256, "partitions_required")
        plan_hash = sha(encode(plan))
        result["plan_sha256"] = plan_hash
        check(m["partition"]["plan_sha256"] == plan_hash, "plan_mismatch")
        by_id, all_rows, all_artifacts, exposure_index = {}, set(), set(), {}
        for part in partitions:
            exact(part, {"partition_id", "artifact_id", "split", "members"}, "plan_partition_fields")
            for key in ("partition_id", "artifact_id"):
                string(part[key], "partition_identity")
            pid = part["partition_id"]
            check(pid not in by_id and part["artifact_id"] not in all_artifacts, "duplicate_partition_or_artifact")
            by_id[pid] = part
            all_artifacts.add(part["artifact_id"])
            check(part["split"] in ("train", "validation", "holdout"), "unknown_split")
            check(type(part["members"]) is list and 0 < len(part["members"]) <= 256, "partition_members")
            for member in part["members"]:
                exact(member, MEMBERSHIP, "plan_member_fields")
                rid = member["row_id"]
                for key in ("row_id", "source_id", "family", "group"):
                    string(member[key], "membership_identity")
                integer(member["seed"], "member_seed")
                check(rid not in all_rows, "duplicate_plan_row", rid)
                all_rows.add(rid)
                if member["exposure"] is None or member["exposure"] == "unknown":
                    raise Refusal("unknown_exposure", rid, "incomplete")
                check(member["exposure"] in ("unexposed", "tuned"), "exposure_value", rid)
                check(part["split"] != "holdout" or member["exposure"] == "unexposed", "tuned_holdout", rid)
                for key in ("source_id", "family", "group"):
                    identity = (key, member[key])
                    prior = exposure_index.setdefault(identity, part["split"])
                    check(prior == part["split"], "cross_partition_" + key, rid)
        pid = m["partition"]["partition_id"]
        string(pid, "partition_id")
        check(pid in by_id, "unknown_partition")
        selected = by_id[pid]
        check(m["artifact_id"] == selected["artifact_id"], "artifact_partition_mismatch")
        members = {x["row_id"]: x for x in selected["members"]}
        row_ids = m["partition"]["row_ids"]
        check(type(row_ids) is list and all(type(x) is str for x in row_ids), "row_ids_type")
        check(len(row_ids) == len(set(row_ids)) and set(row_ids) == set(members), "partition_row_set")
        check(len(rows) == len(members), "partition_row_count")
        result["checks"].append({"id": "frozen_provenance_partition", "status": "passed"})
        seen = set()
        for row in rows:
            exact(row, MEMBERSHIP | {"partition_id", "split", "boundary", "window", "quantities"}, "row_fields")
            rid = row["row_id"]
            string(rid, "row_id")
            check(rid in members and rid not in seen, "unknown_or_duplicate_row", rid)
            seen.add(rid)
            check(same({k: row[k] for k in MEMBERSHIP}, members[rid]), "row_membership_mismatch", rid)
            check(row["partition_id"] == pid and row["split"] == selected["split"], "row_partition_mismatch", rid)
            check(row["seed"] == provenance["seed"] and row["family"] == scope["scenario_family"], "row_provenance_mismatch", rid)
            check(row["boundary"] == scope["boundary"] and row["window"] == scope["window"], "domain_boundary_window", rid)
            q = row["quantities"]
            exact(q, profile["fields"], "quantity_fields", rid)
            for name, unit in profile["fields"].items():
                exact(q[name], {"value", "unit"}, "quantity_shape", rid)
                check(q[name]["unit"] == unit, "unit_consistency", rid)
                integer(q[name]["value"], "nonnegative_integer", rid)
            check(q["available"]["value"] == q["allocated"]["value"] + q["idle"]["value"], "resource_conservation", rid)
            for invariant in ("nonnegative_integer", "unit_consistency", "resource_conservation"):
                result["checks"].append({"id": invariant, "row_id": rid, "status": "passed"})
        result["admission"] = "accepted_static"
    except Refusal as exc:
        result["admission"] = exc.status
        result["checks"].append({"id": exc.code, "status": "incomplete" if exc.status == "incomplete" else "failed"})
        if exc.row is not None:
            result["failing_row_ids"] = [exc.row]
    except (ValueError, TypeError, OverflowError, RecursionError, KeyError) as exc:
        result["checks"].append({"id": "malformed_input", "status": "failed", "detail": str(exc)[:240]})
    return result


def consume_supplied_batch(manifest_bytes, payload_bytes, trusted_profile, admission_bytes, *, replay_store=None):
    """Return rows only after fresh admission and exact comparison with the receipt.

    Receipt flags alone never grant access. This gate authenticates neither the
    trust issuer nor observations. Its caller must retain the frozen trust identity.
    """
    fresh = admit_supplied_batch(manifest_bytes, payload_bytes, trusted_profile)
    check(fresh["admission"] == "accepted_static", "consumer_admission_refused")
    # An accepted receipt has two batch checks plus three checks for each row:
    # up to 770 checks for 256 admitted rows. Only this top-level array may use
    # that derived bound; all supplied input arrays retain their 256-entry cap.
    # Each row ID also repeats in three checks. Size the receipt-only byte budget
    # from our fresh result so any emitted canonical receipt can round-trip.
    receipt_bytes = max(MAX_BYTES, len(encode(fresh)))
    check(type(admission_bytes) is bytes and same(
        load(admission_bytes, root_array_limits={"checks": len(fresh["checks"])},
             max_bytes=receipt_bytes), fresh),
        "consumer_result_mismatch")
    check(replay_store is None, 'caller_replay_store_refused')
    claim_designated([(manifest_bytes, payload_bytes)])
    return load(payload_bytes)["rows"]


class ReplayStore:
    """Owner-provisioned replay registry; trust authentication remains external."""
    def __init__(self, path, *, create=False):
        import os
        import sqlite3
        from pathlib import Path
        from urllib.parse import quote
        check(str(path) != ':memory:', 'persistent_replay_store_required')
        if create:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        uri = 'file:' + quote(str(Path(path).resolve()), safe='/') + '?mode=rw'
        self.db = sqlite3.connect(uri, uri=True, timeout=5)
        self.db.execute('PRAGMA synchronous=FULL')
        if create:
            import uuid
            self.db.execute('CREATE TABLE registry (identity TEXT NOT NULL)')
            self.db.execute('INSERT INTO registry VALUES(?)', (str(uuid.uuid4()),))
            self.db.execute('CREATE TABLE consumed (identity TEXT PRIMARY KEY)')
            self.db.execute('CREATE TABLE journal (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, head TEXT NOT NULL)')
            self.db.commit()
        check(self.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'replay_integrity')
        self.db.execute('SELECT identity FROM consumed LIMIT 1')

    def __enter__(self): return self
    def __exit__(self, *args): self.db.close()

    def claim_many(self, batches):
        import sqlite3
        identities = []
        for manifest_bytes, payload_bytes in batches:
            manifest, payload = load(manifest_bytes), load(payload_bytes)
            scope = manifest['scope']
            prefix = [scope['repository'], scope['snapshot']]
            identities.append(sha(encode(['artifact', *prefix, manifest['artifact_id']])))
            identities.append(sha(encode(['payload', sha(payload_bytes)])))
            for row in payload['rows']:
                identities.append(sha(encode(['row', *prefix, row['row_id']])))
                identities.append(sha(encode(['source', *prefix, row['source_id'], row['group']])))
        check(len(identities) == len(set(identities)), 'cross_call_replay')
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                for identity in identities:
                    self.db.execute('INSERT INTO consumed VALUES(?)', (identity,))
                    _append_entry(self, 'consume', identity)
        except sqlite3.IntegrityError as exc:
            raise Refusal('cross_call_replay') from exc


def _journal(store):
    identity = store.db.execute('SELECT identity FROM registry').fetchall()
    check(len(identity) == 1, 'replay_identity_invalid')
    head = sha(encode(['replay/v1', identity[0][0]]))
    genesis = head
    import sqlite3
    try:
        rows = store.db.execute('SELECT seq,kind,body,previous,head FROM journal ORDER BY seq').fetchall()
    except sqlite3.OperationalError as exc:
        if store.db.execute("SELECT name FROM sqlite_master WHERE name='journal'").fetchone():
            raise Refusal('replay_chain_invalid') from exc
        rows = []
        previous = genesis
        for position, (value,) in enumerate(store.db.execute('SELECT identity FROM consumed ORDER BY rowid'), 1):
            recorded = sha(encode([previous, position, value]))
            rows.append((position, 'consume', json.dumps(value), previous, recorded))
            previous = recorded
    consumed, entries = [], []
    for position, (seq, kind, body, previous, recorded) in enumerate(rows, 1):
        check(seq == position and previous == head and kind in ('consume', 'recovery'), 'replay_chain_invalid')
        value = json.loads(body)
        head = sha(encode([head, position, value if kind == 'consume' else {'recovery': value}]))
        check(head == recorded, 'replay_chain_invalid')
        if kind == 'consume':
            check(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'replay_chain_invalid')
            consumed.append(value)
        entries.append(dict(seq=seq, kind=kind, value=value, head=head))
    actual = [row[0] for row in store.db.execute('SELECT identity FROM consumed ORDER BY rowid')]
    check(actual == consumed, 'replay_consumptions_mismatch')
    return dict(identity=identity[0][0], head=head, count=len(rows)), genesis, entries


def _store_head(store):
    return _journal(store)[0]


def _append_entry(store, kind, value):
    identity = store.db.execute('SELECT identity FROM registry').fetchone()[0]
    row = store.db.execute('SELECT seq,head FROM journal ORDER BY seq DESC LIMIT 1').fetchone()
    position, previous = (row[0]+1, row[1]) if row else (1, sha(encode(['replay/v1', identity])))
    head = sha(encode([previous, position, value if kind == 'consume' else {'recovery': value}]))
    store.db.execute('INSERT INTO journal VALUES(?,?,?,?,?)',
                     (position, kind, encode(value).decode(), previous, head))


def provision_replay_store(path, anchor):
    """Owner-only setup; retain the anchor outside candidate-controlled storage."""
    import os
    from pathlib import Path
    with ReplayStore(path, create=True) as store:
        state = dict(path=str(Path(path).resolve()), **_store_head(store))
    fd = os.open(anchor, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(encode(state)); stream.flush(); os.fsync(stream.fileno())
    _sync_directory(Path(anchor).parent)
    return state


def _sync_directory(path):
    import os
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def claim_designated(batches):
    """Consume using evaluator configuration, never a store supplied with evidence."""
    import os
    import fcntl
    import tempfile
    from pathlib import Path
    configured = os.environ.get('DIMAGGI_RSI_REPLAY_ANCHOR')
    check(bool(configured), 'pinned_replay_store_required')
    anchor = Path(configured)
    # A separate lock survives atomic replacement of the anchor.
    with open(str(anchor) + '.lock', 'a+b') as lock:
        _lock_replay(lock)
        state = load(anchor.read_bytes())
        check(set(state) == {'path', 'identity', 'head', 'count'}, 'replay_anchor_invalid')
        with ReplayStore(state['path']) as store:
            check(_store_head(store) == {k: state[k] for k in ('identity', 'head', 'count')},
                  'replay_anchor_mismatch')
            check(store.db.execute("SELECT name FROM sqlite_master WHERE name='journal'").fetchone(), 'replay_journal_required')
            ReplayStore.claim_many(store, batches)
            updated = dict(path=state['path'], **_store_head(store))
        _write_anchor(anchor, updated)


def _lock_replay(lock):
    import fcntl
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise Refusal('replay_busy') from exc


def _write_anchor(anchor, updated):
    import os
    import tempfile
    from pathlib import Path
    fd, temporary = tempfile.mkstemp(dir=anchor.parent, prefix='.replay-anchor-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(encode(updated)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, anchor)
        _sync_directory(anchor.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def recover_replay_store(anchor, *, confirm=False):
    """Verify an anchored journal prefix; retain all consumption on recovery."""
    from pathlib import Path
    anchor = Path(anchor)
    with open(str(anchor) + '.lock', 'a+b') as lock:
        _lock_replay(lock)
        state = load(anchor.read_bytes())
        check(set(state) == {'path', 'identity', 'head', 'count'}, 'replay_anchor_invalid')
        with ReplayStore(state['path']) as store:
            with store.db:
                store.db.execute('BEGIN IMMEDIATE')
                current, genesis, entries = _journal(store)
                count = state['count']
                check(type(count) is int and 0 <= count <= current['count'], 'replay_anchor_mismatch')
                prefix = entries[count-1]['head'] if count else genesis
                check(state['identity'] == current['identity'] and state['head'] == prefix, 'replay_anchor_mismatch')
                report = dict(anchor=state, database=current, ahead=entries[count:])
                legacy = not store.db.execute("SELECT name FROM sqlite_master WHERE name='journal'").fetchone()
                check(bool(report['ahead']) or legacy, 'replay_recovery_not_needed')
                if confirm is not True:
                    refusal = Refusal('owner_confirmation_required')
                    refusal.report = report
                    raise refusal
                if not store.db.execute("SELECT name FROM sqlite_master WHERE name='journal'").fetchone():
                    store.db.execute('CREATE TABLE journal (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, previous TEXT NOT NULL, head TEXT NOT NULL)')
                    for entry in entries: _append_entry(store, entry['kind'], entry['value'])
                    check(_store_head(store) == current, 'replay_chain_invalid')
                _append_entry(store, 'recovery', report)
                updated = dict(path=state['path'], **_store_head(store))
            _write_anchor(anchor, updated)
            return dict(report, recovered=updated)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Inspect and recover an interrupted replay anchor update.')
    parser.add_argument('--recover-anchor', required=True)
    parser.add_argument('--confirm-owner-recovery', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(recover_replay_store(args.recover_anchor, confirm=args.confirm_owner_recovery), sort_keys=True))
        return 0
    except Refusal as exc:
        print(json.dumps(dict(error=exc.code, report=getattr(exc, 'report', None)), sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
