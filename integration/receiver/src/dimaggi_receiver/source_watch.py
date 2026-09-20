"""Approved-source change collection and quarantine proposals; never activation."""

import hashlib
from urllib.parse import urlsplit
from .bounded_fetch import https_get
from .infrastructure import array, obj, sha, stamp, text
from .jsonio import digest, canonical

HOSTS = {
    "docs.cloud.google.com",
    "cloud.google.com",
    "docs.nvidia.com",
    "developer.nvidia.com",
}


def validate_config(config):
    obj(config, ("schema", "sources"), "source watch config")
    if config["schema"] != "dimaggi-source-watch-config/v1":
        raise ValueError("unsupported watch configuration")
    ids = set()
    for s in array(config["sources"], "sources"):
        obj(s, ("id", "url", "profile_ids", "max_bytes"), "source")
        text(s["id"], "source id")
        if s["id"] in ids:
            raise ValueError("duplicate source id")
        ids.add(s["id"])
        u = urlsplit(s["url"])
        if (
            u.scheme != "https"
            or u.hostname not in HOSTS
            or u.netloc != u.hostname
            or u.query
            or u.fragment
            or not u.path.startswith("/")
        ):
            raise ValueError("source URL outside approved official HTTPS scope")
        if (
            type(s["max_bytes"]) is not int
            or not 1 <= s["max_bytes"] <= 4 * 1024 * 1024
        ):
            raise ValueError("source byte limit")
        array(s["profile_ids"], "profile_ids")
        for p in s["profile_ids"]:
            text(p, "profile id")
        if len(set(s["profile_ids"])) != len(s["profile_ids"]):
            raise ValueError("duplicate impact mapping")
    if len(ids) > 16:
        raise ValueError("source collection budget exceeds 16")


def collect(config, observed_at, fetch=https_get):
    validate_config(config)
    stamp(observed_at)
    records = []
    for s in config["sources"]:
        try:
            raw, kind = fetch(s["url"], timeout=15, limit=s["max_bytes"])
            if (
                not raw
                or len(raw) > s["max_bytes"]
                or kind
                not in {
                    "text/html",
                    "text/plain",
                    "application/json",
                    "application/xml",
                    "text/xml",
                }
            ):
                raise ValueError("source format refused")
            # Hash exact returned bytes. Navigation changes can cause conservative
            # review noise; never discard unseen changes through fuzzy matching.
            records.append(
                dict(
                    id=s["id"],
                    url=s["url"],
                    status="collected",
                    sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
                    bytes=len(raw),
                    content_type=kind,
                    profile_ids=s["profile_ids"],
                )
            )
        except Exception:
            records.append(
                dict(
                    id=s["id"],
                    url=s["url"],
                    status="unavailable",
                    sha256=None,
                    bytes=0,
                    content_type=None,
                    profile_ids=s["profile_ids"],
                )
            )
    result = dict(
        schema="dimaggi-source-snapshot/v1",
        config_digest=digest(config),
        observed_at=observed_at,
        status=(
            "complete"
            if all(r["status"] == "collected" for r in records)
            else "incomplete"
        ),
        sources=records,
        execution_authorized=False,
        activation_authorized=False,
    )
    result["snapshot_digest"] = digest(result)
    return result


def check_snapshot(value):
    obj(
        value,
        (
            "schema",
            "config_digest",
            "observed_at",
            "status",
            "sources",
            "execution_authorized",
            "activation_authorized",
            "snapshot_digest",
        ),
        "source snapshot",
    )
    if (
        value["schema"] != "dimaggi-source-snapshot/v1"
        or value["execution_authorized"] is not False
        or value["activation_authorized"] is not False
    ):
        raise ValueError("invalid snapshot boundary")
    sha(value["config_digest"])
    stamp(value["observed_at"])
    if value["snapshot_digest"] != digest(
        {k: v for k, v in value.items() if k != "snapshot_digest"}
    ):
        raise ValueError("snapshot digest mismatch")
    ids = set()
    for r in array(value["sources"], "sources"):
        obj(
            r,
            ("id", "url", "status", "sha256", "bytes", "content_type", "profile_ids"),
            "source result",
        )
        text(r["id"], "source id")
        if r["id"] in ids:
            raise ValueError("duplicate snapshot source")
        ids.add(r["id"])
        if r["status"] == "collected":
            sha(r["sha256"])
            if type(r["bytes"]) is not int or not 0 < r["bytes"] <= 4 * 1024 * 1024:
                raise ValueError("invalid source size")
        elif r["status"] != "unavailable" or r["sha256"] is not None or r["bytes"] != 0:
            raise ValueError("invalid source result")
    actual = (
        "complete"
        if all(s["status"] == "collected" for s in value["sources"])
        else "incomplete"
    )
    if actual != value["status"]:
        raise ValueError("inconsistent collection completeness")


def compare(config, baseline, current):
    validate_config(config)
    check_snapshot(baseline)
    check_snapshot(current)
    pin = digest(config)
    if baseline["config_digest"] != pin or current["config_digest"] != pin:
        raise ValueError("source configuration changed; establish reviewed baseline")
    if stamp(current["observed_at"]) < stamp(baseline["observed_at"]):
        raise ValueError("source clock rollback")
    expected = {s["id"]: s for s in config["sources"]}
    for snap in (baseline, current):
        if {s["id"] for s in snap["sources"]} != set(expected):
            raise ValueError("source coverage mismatch")
        for s in snap["sources"]:
            if any(s[k] != expected[s["id"]][k] for k in ("url", "profile_ids")):
                raise ValueError("source attribution changed")
    old = {s["id"]: s for s in baseline["sources"]}
    findings = []
    for s in current["sources"]:
        before = old[s["id"]]
        if s["status"] != "collected" or before["status"] != "collected":
            kind = "collection_incomplete"
        elif s["sha256"] != before["sha256"]:
            kind = "source_changed"
        else:
            continue
        finding = dict(
            source_id=s["id"],
            kind=kind,
            before_digest=before["sha256"],
            after_digest=s["sha256"],
            affected_profiles=s["profile_ids"],
            required_action="quarantine_review",
        )
        finding["finding_id"] = digest(
            finding
        )  # stable across polls; no repeated new credit
        findings.append(finding)
    return dict(
        schema="dimaggi-source-watch-report/v1",
        observed_at=current["observed_at"],
        baseline_digest=baseline["snapshot_digest"],
        current_digest=current["snapshot_digest"],
        status=(
            "incomplete"
            if baseline["status"] != "complete" or current["status"] != "complete"
            else "changes" if findings else "no_change"
        ),
        findings=findings,
        execution_authorized=False,
        activation_authorized=False,
        mutation_request=None,
    )


def check_queue(queue):
    obj(
        queue,
        (
            "schema",
            "config_digest",
            "observed_at",
            "profile_ids",
            "findings",
            "execution_authorized",
            "activation_authorized",
            "queue_digest",
        ),
        "review queue",
    )
    if (
        queue["schema"] != "dimaggi-source-review-queue/v1"
        or queue["execution_authorized"] is not False
        or queue["activation_authorized"] is not False
    ):
        raise ValueError("invalid review queue boundary")
    sha(queue["config_digest"])
    stamp(queue["observed_at"])
    for profile in array(queue["profile_ids"], "watched profiles"):
        text(profile, "profile id")
    if queue["queue_digest"] != digest(
        {k: v for k, v in queue.items() if k != "queue_digest"}
    ):
        raise ValueError("review queue digest mismatch")
    seen = set()
    # A persistent queue has a bounded size. Overflow stops the watcher; it must
    # never drop pending reviews to stay within its storage budget.
    for f in array(queue["findings"], "findings", nonempty=False):
        obj(
            f,
            (
                "source_id",
                "kind",
                "before_digest",
                "after_digest",
                "affected_profiles",
                "required_action",
                "finding_id",
            ),
            "finding",
        )
        if (
            f["finding_id"] != digest({k: v for k, v in f.items() if k != "finding_id"})
            or f["finding_id"] in seen
        ):
            raise ValueError("finding identity mismatch")
        seen.add(f["finding_id"])
        text(f["source_id"], "source id")
        if (
            f["kind"] not in {"source_changed", "collection_incomplete"}
            or f["required_action"] != "quarantine_review"
        ):
            raise ValueError("invalid finding")
        for key in ("before_digest", "after_digest"):
            if f[key] is not None:
                sha(f[key])
        for profile in array(f["affected_profiles"], "affected profiles"):
            text(profile, "profile id")


def review_queue(config, report, previous=None):
    """Append-only unresolved reviews. Recovery or reversion never clears one."""
    validate_config(config)
    findings = {}
    if previous is not None:
        check_queue(previous)
        if previous["config_digest"] != digest(config):
            raise ValueError("review configuration changed")
        findings = {f["finding_id"]: f for f in previous["findings"]}
    for f in report["findings"]:
        findings[f["finding_id"]] = f
    result = dict(
        schema="dimaggi-source-review-queue/v1",
        config_digest=digest(config),
        observed_at=report["observed_at"],
        profile_ids=sorted(
            {p for source in config["sources"] for p in source["profile_ids"]}
        ),
        findings=[findings[k] for k in sorted(findings)],
        execution_authorized=False,
        activation_authorized=False,
    )
    result["queue_digest"] = digest(result)
    check_queue(result)
    return result
