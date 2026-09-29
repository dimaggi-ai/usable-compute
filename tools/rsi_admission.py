"""Internal synthetic supplied-byte admission. No artifact code or reference is run.

The caller must supply independently frozen trust bytes; hashes do not authenticate
that caller. Only the illustrative resource-count profile is implemented. Records
v1 consumption requires admission and an owner-provisioned persistent replay store.
"""
import hashlib
import json
import re
from rsi_records import load, REPOSITORIES


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
    check(type(admission_bytes) is bytes and same(load(admission_bytes), fresh), "consumer_result_mismatch")
    check(isinstance(replay_store, ReplayStore), 'persistent_replay_store_required')
    replay_store.claim_many([(manifest_bytes, payload_bytes)])
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
            self.db.execute('CREATE TABLE consumed (identity TEXT PRIMARY KEY)')
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
                self.db.executemany('INSERT INTO consumed VALUES(?)', [(identity,) for identity in identities])
        except sqlite3.IntegrityError as exc:
            raise Refusal('cross_call_replay') from exc
