import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
WORKSPACE = Path(os.environ.get("MAGGIE_WORKSPACE_ROOT", HERE.parents[5])).resolve()


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("domain-review")
    subprocess.run([sys.executable, str(HERE / "review.py"), "--workspace-root", str(WORKSPACE), "--out", str(out)],
                   text=True, capture_output=True, check=True)
    return out


def load(out, name):
    return json.loads((out / name).read_text())


def test_all_finite_joined_results_preserved(report):
    runs = load(report, "joined-compatibility.json")
    assert runs["pairs"] == 18
    assert runs["baseline_runs"] == runs["corrected_runs"]
    old = load(WORKSPACE / "strategy/team/2026-09-19-maggie-first-decision/evidence", "selected-runs.json")
    for name, value in old.items():
        assert runs["baseline_runs"][name + "/seed-0"] == value


def test_known_issue_has_a_distinct_corrected_result(report):
    probe = load(report, "cooling-correction.json")
    assert sum(row["verdict"] == "ADMIT" for row in probe["pinned_known_defect"]) == 4
    assert sum(row["outcome"] == "invalid_input" for row in probe["corrected_working_results"]) == 4
    assert sum(row["outcome"] == "DENY" for row in probe["corrected_working_results"]) == 4
    result = load(report, "review-report.json")
    assert result["execution_authorized"] is False and result["mutation_request"] is None
    assert result["cpu_executor"] == "not_run"


def test_review_hashes_match_actual_bytes(report):
    manifest = load(report, "provenance.json")
    for name, expected in manifest["working_source_sha256"].items():
        assert hashlib.sha256((WORKSPACE / name).read_bytes()).hexdigest() == expected
    for name, expected in manifest["artifact_sha256"].items():
        assert hashlib.sha256((report / name).read_bytes()).hexdigest() == expected
