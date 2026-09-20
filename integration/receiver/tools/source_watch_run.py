"""Bounded source collection with retained bytes and append-only review findings.

An exclusive history lock prevents concurrent runs from losing pending reviews.
Changed and unavailable sources remain quarantined after recovery or reversion.
No source edit, profile activation, notification or execution is performed.
"""

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
from pathlib import Path
from dimaggi_receiver.bounded_fetch import https_get
from dimaggi_receiver.jsonio import loads, read_file, dumps
from dimaggi_receiver.source_watch import (
    collect,
    compare,
    validate_config,
    review_queue,
)


def run(config, history, fetch=https_get):
    validate_config(config)
    history = Path(history).resolve()
    history.mkdir(parents=True, exist_ok=True)
    with (history / ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = sorted(history.glob("*/snapshot.json"))
        retained_bytes = sum(
            p.stat().st_size for p in history.glob("*/*") if p.is_file()
        ) + sum(p.stat().st_size for p in history.glob("*/sources/*") if p.is_file())
        next_budget = sum(source["max_bytes"] for source in config["sources"])
        if len(prior) >= 90 or retained_bytes + next_budget > 256 * 1024 * 1024:
            raise ValueError(
                "history budget reached; archive with review queue preserved before resuming"
            )
        baseline = previous = None
        if prior:
            baseline = loads(read_file(prior[-1]).decode())
            # A interrupted run with no queue is an explicit recovery condition.
            # Never skip it and accidentally lose an unresolved change.
            previous = loads(read_file(prior[-1].with_name("queue.json")).decode())
        now = datetime.now(timezone.utc)
        output = history / now.strftime("%Y%m%dT%H%M%S.%fZ")
        output.mkdir(exist_ok=False)
        (output / "sources").mkdir()

        def retain(url, **kwargs):
            raw, kind = fetch(url, **kwargs)
            digest = hashlib.sha256(raw).hexdigest()
            (output / "sources" / digest).write_bytes(raw)
            return raw, kind

        snapshot = collect(config, now.strftime("%Y-%m-%dT%H:%M:%SZ"), retain)
        (output / "snapshot.json").write_text(dumps(snapshot))
        report = compare(config, baseline or snapshot, snapshot)
        report["bootstrap"] = baseline is None
        queue = review_queue(config, report, previous)
        (output / "report.json").write_text(dumps(report))
        (output / "queue.json").write_text(dumps(queue))
        result = dict(
            run=str(output),
            status=report["status"],
            pending_reviews=len(queue["findings"]),
            activation_authorized=False,
        )
        (output / "receipt.json").write_text(dumps(result))
        return result, 0 if snapshot["status"] == "complete" else 2


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--history", required=True, type=Path)
    a = p.parse_args()
    result, code = run(loads(read_file(a.config).decode()), a.history)
    print(dumps(result), end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
