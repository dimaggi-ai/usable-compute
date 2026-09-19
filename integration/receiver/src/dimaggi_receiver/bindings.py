"""Bind receiver bytes to the non-executing Tool Guard preview transport."""
from __future__ import annotations

import hashlib

from .jsonio import loads, validate_unicode
from .report import validate_report


def policy_input(report_bytes, request_id):
    if not isinstance(request_id, str) or not request_id.strip() or request_id.strip() != request_id:
        raise ValueError("request_id must be nonempty text without outer whitespace")
    validate_unicode(request_id)
    report = validate_report(loads(report_bytes.decode("utf-8")))
    action = report["action_intent"]
    binding = {key: report[key] for key in ("report_id", "profile_id", "profile_digest", "evidence_class",
                                           "evidence_digest", "selected_request", "execution_readiness")}
    binding.update(report_digest="sha256:" + hashlib.sha256(report_bytes).hexdigest(),
                   action_class=action["action_class"], cardinality=action["cardinality"],
                   target_scope=action["designated_scheduler_scope"], payload_digest=action["workload_artifact_digest"])
    return {"schema": "dimaggi-batch-preview/v1", "request_id": request_id,
            "report_binding": binding, "expected_binding": None,
            "approval": {"status": "not_requested", "binding_digest": None, "expires_at_utc": None},
            "evidence_expires_at_utc": None}
