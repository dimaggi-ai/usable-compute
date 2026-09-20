"""Record an explicit, pinned no-material-change review without activating profiles.

Retains the prior snapshot time: reviewing evidence is not a new source fetch.
Only a trusted history owner may use this command. The reviewer's evidence is
retained verbatim; a digest cannot establish that their assessment is true.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
from pathlib import Path
from dimaggi_receiver.jsonio import digest, dumps, loads, read_file
from dimaggi_receiver.source_watch import check_queue, check_snapshot
from dimaggi_receiver.infrastructure import text


def review(history, expected_queue_digest, finding_ids, evidence, reviewer):
    history = Path(history).resolve()
    text(reviewer, "reviewer")
    evidence = read_file(evidence)
    if not evidence.strip():
        raise ValueError("review evidence required")
    if not finding_ids or len(set(finding_ids)) != len(finding_ids):
        raise ValueError("unique finding IDs required")
    with (history / ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior = sorted(history.glob("*/snapshot.json"))
        if not prior or len(prior) >= 90:
            raise ValueError("review history missing or full")
        latest = prior[-1].parent
        snapshot = loads(read_file(latest / "snapshot.json").decode())
        check_snapshot(snapshot)
        queue = loads(read_file(latest / "queue.json").decode())
        check_queue(queue)
        if queue["queue_digest"] != expected_queue_digest:
            raise ValueError("review queue changed; re-read before reviewing")
        if not set(finding_ids) <= {f["finding_id"] for f in queue["findings"]}:
            raise ValueError("unknown finding")
        updated = deepcopy(queue)
        updated["findings"] = [
            f for f in queue["findings"] if f["finding_id"] not in finding_ids
        ]
        updated["queue_digest"] = digest(
            {k: v for k, v in updated.items() if k != "queue_digest"}
        )
        check_queue(updated)
        output = history / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-review"
        )
        output.mkdir(exist_ok=False)
        (output / "sources").mkdir()
        for source in snapshot["sources"]:
            if source["status"] == "collected":
                name = source["sha256"].split(":")[1]
                raw = read_file(latest / "sources" / name)
                if hashlib.sha256(raw).hexdigest() != name:
                    raise ValueError("reviewed source bytes changed")
                (output / "sources" / name).write_bytes(raw)
        (output / "review-evidence.txt").write_bytes(evidence)
        receipt = dict(
            schema="dimaggi-source-review/v1",
            reviewer=reviewer,
            decision="no_material_change",
            finding_ids=sorted(finding_ids),
            before_queue_digest=expected_queue_digest,
            after_queue_digest=updated["queue_digest"],
            evidence_digest="sha256:" + hashlib.sha256(evidence).hexdigest(),
            source_snapshot_digest=snapshot["snapshot_digest"],
            source_observed_at=snapshot["observed_at"],
            activation_authorized=False,
            execution_authorized=False,
        )
        (output / "snapshot.json").write_text(dumps(snapshot))
        (output / "queue.json").write_text(dumps(updated))
        (output / "report.json").write_text(dumps(receipt))
        return dict(
            run=str(output), pending_reviews=len(updated["findings"]), review=receipt
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history", required=True, type=Path)
    p.add_argument("--queue-digest", required=True)
    p.add_argument("--finding-id", action="append", required=True)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--reviewer", required=True)
    a = p.parse_args()
    print(
        dumps(review(a.history, a.queue_digest, a.finding_id, a.evidence, a.reviewer)),
        end="",
    )


if __name__ == "__main__":
    main()
