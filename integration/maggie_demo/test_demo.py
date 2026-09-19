"""Verify the composed artifact, preserved baseline and non-executable claims."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
WORKSPACE = (Path(os.environ["MAGGIE_WORKSPACE_ROOT"]).resolve()
             if "MAGGIE_WORKSPACE_ROOT" in os.environ else HERE.parents[5])


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("maggie-demo")
    subprocess.run([sys.executable, str(HERE / "demo.py"), "--workspace-root", str(WORKSPACE), "--out", str(out)],
                   check=True, capture_output=True, text=True)
    return out


def load(out, name):
    return json.loads((out / name).read_text())


def test_model_results_equal_preexisting_frozen_package(report):
    baseline = WORKSPACE / "strategy/team/2026-09-19-maggie-first-decision/evidence/selected-runs.json"
    assert load(report, "model-runs.json") == json.loads(baseline.read_text())
    assert len(load(report, "model-runs.json")["baseline"]["jobs"]) == 4
    assert len(load(report, "model-runs.json")["smaller_gang"]["jobs"]) == 8
    narrative = (report / "Demo.md").read_text()
    assert "64-chip row has eight" in narrative
    for result in load(report, "model-runs.json").values():
        assert sum(result["buckets"].values()) == pytest.approx(368640, abs=1e-6)


def test_domain_examples_are_separate_and_non_executable(report):
    resource = load(report, "resource-cases.json")
    calibration = load(report, "calibration-cases.json")
    checkpoint = load(report, "checkpoint-cases.json")
    assert len(resource["results"]) == 16
    assert len(calibration["cases"]) == 15
    interval = checkpoint["interval_results"][0]
    assert interval["buckets"] == {"recovery_gpu_s": 40, "checkpoint_only_gpu_s": 10, "healthy_gpu_s": 150}
    assert interval["total_gpu_s"] == 200
    for row in checkpoint["phase_results"] + checkpoint["interval_results"]:
        assert row["execution_authorized"] is False and row["mutation_request"] is None
    claims = load(report, "claims.json")
    assert len({c["id"] for c in claims["claims"]}) == 8
    assert all(c["limit"] for c in claims["claims"])
    assert claims["execution_authorized"] is False and claims["mutation_request"] is None


def test_manifest_hashes_and_import_origins(report):
    manifest = load(report, "provenance.json")
    for name, expected in manifest["artifact_sha256"].items():
        assert hashlib.sha256((report / name).read_bytes()).hexdigest() == expected
    for name, expected in manifest["working_reference_sha256"].items():
        assert hashlib.sha256((WORKSPACE / name).read_bytes()).hexdigest() == expected
    for name in manifest["imported_model_files"].values():
        assert "/_Audits/2026-09-17-repo-aware-strategy/repos/" in name
    assert all(row["exit_code"] == 0 for row in manifest["reference_commands"])


def test_existing_output_is_preserved(report):
    before = {p.name: p.read_bytes() for p in report.iterdir()}
    result = subprocess.run([sys.executable, str(HERE / "demo.py"), "--workspace-root", str(WORKSPACE), "--out", str(report)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "earlier evidence is preserved" in result.stderr
    assert before == {p.name: p.read_bytes() for p in report.iterdir()}
