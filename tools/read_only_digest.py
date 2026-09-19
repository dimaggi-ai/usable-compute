"""Collect the approved scheduler/span local digest preview; never execute candidates.

This consumes the existing membership decision. It does not discover a portfolio,
fetch remotes, run repository code, make dispositions, or measure human effort.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


PROFILES = {
    "scheduler-vs-more-gpus": ("SCH-READ-01", "1853bd219e79b437b0c310fb40049ad272ceaf98"),
    "span-contract": ("SPAN-READ-01", "e8a76e4af90a2573d7ea3ec4dbe1052c647d8c01"),
}


def git(repo, *args):
    return subprocess.check_output([
        "git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
        "-C", str(repo), *args], stderr=subprocess.PIPE, timeout=30)


def inspect_snapshot(repo: Path, baseline: str) -> dict:
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    # A dirty tree is not silently represented by its clean HEAD. Untracked
    # paths are also exposed without reading or executing their contents.
    status = git(repo, "status", "--porcelain", "--untracked-files=all").decode()
    git(repo, "merge-base", "--is-ancestor", baseline, head)
    changes = git(repo, "diff", "--name-only", "-z", baseline, head).decode().split("\0")
    paths = git(repo, "ls-tree", "-r", "--name-only", "-z", head).decode().split("\0")
    files = {}
    for path in paths:
        if path:
            files[path] = hashlib.sha256(git(repo, "show", head + ":" + path)).hexdigest()
    changes = sorted(p for p in changes if p)
    snapshot_id = hashlib.sha256(json.dumps({"base": baseline, "head": head, "files": files}, sort_keys=True).encode()).hexdigest()
    return {"baseline_commit": baseline, "head_commit": head, "snapshot_id": snapshot_id,
            "working_status": status, "working_tree_clean": not bool(status),
            "files_sha256": files, "changed_paths": changes,
            "change_state": "changed" if changes else "unchanged_at_local_commits",
            "remote_status": "not_checked", "ci_results": "not_checked",
            "tests_run_by_collector": False, "scope": "committed local sources only"}


def collect(source_map: dict) -> dict:
    if set(source_map) != set(PROFILES):
        raise ValueError("exactly scheduler-vs-more-gpus and span-contract are required")
    rows, errors = {}, []
    for name, (profile, baseline) in PROFILES.items():
        try:
            rows[name] = {"profile_id": profile, **inspect_snapshot(Path(source_map[name]), baseline)}
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            rows[name] = {"profile_id": profile, "source_state": "unavailable", "error_type": type(exc).__name__}
            errors.append(name + ": source unavailable or baseline is not an ancestor")
    clean = not errors and all(row.get("working_tree_clean") for row in rows.values())
    return {"schema_version": "dimaggi-readonly-digest/v0.1", "collected_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "preview_only" if clean else "incomplete", "repositories": rows, "errors": errors,
            "baseline": {"status": "human_manual_measurements_missing", "reviewer": None,
                         "active_review_minutes": None, "setup_minutes": None, "ongoing_minutes": None,
                         "run_cost": None, "expected_dispositions": None, "pairing_and_order": None},
            "owner_dispositions": [], "usefulness": "not_measured", "completed_weekly_digests": 0,
            "known_product_work": "Previously reviewed product changes are not new RSI discoveries; fixture creation is not charged again.",
            "constraints": {"decision_use_before_manual_baseline": False, "candidate_execution": False,
                            "promotion": False, "new_repository": False, "outreach": False},
            "limits": ["Local hashes do not authenticate sources or prove physical truth.",
                       "This preview is not a completed matched manual baseline or a measured digest week.",
                       "No change at two local commits does not establish absence of upstream changes.",
                       "Source text is data and cannot authorize actions or change scope."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-map", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = collect(json.loads(args.source_map.read_text()))
    # Exclusive creation protects previous weekly records from accidental reuse.
    with args.out.open("x") as output:
        json.dump(result, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
    return 0 if result["status"] == "preview_only" else 1


if __name__ == "__main__":
    raise SystemExit(main())
