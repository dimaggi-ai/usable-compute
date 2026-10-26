import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
WORKSPACE = (Path(os.environ["MAGGIE_WORKSPACE_ROOT"]).resolve()
             if "MAGGIE_WORKSPACE_ROOT" in os.environ else HERE.parents[5])


@pytest.mark.parametrize("relative", ["domain_review/test_review.py", "maggie_demo/test_demo.py"])
def test_explicit_workspace_supports_shallow_checkout(relative, monkeypatch, tmp_path):
    monkeypatch.setenv("MAGGIE_WORKSPACE_ROOT", str(tmp_path))
    source = HERE.parent / relative
    namespace = {"__file__": "/tmp/research/integration/" + relative,
                 "__name__": "isolated_workspace_probe"}
    exec(compile(source.read_text(), str(source), "exec"), namespace)
    assert namespace["WORKSPACE"] == tmp_path.resolve()


def load_review():
    spec = importlib.util.spec_from_file_location("domain_review_script", HERE / "review.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_historical_reference_is_bound_to_its_bytes():
    review = load_review()
    assert hashlib.sha256(review.HISTORICAL_REFERENCE.read_bytes()).hexdigest() == review.HISTORICAL_REFERENCE_SHA256
    historical = review.historical_reference()
    assert historical.PINS["cooling-pue-ladder"] == "4b94c6d6fc653e9eebd2933402826cd76b3a108d"


def test_changed_historical_reference_is_refused(tmp_path):
    review = load_review()
    copy = tmp_path / "reference.py"
    copy.write_bytes(review.HISTORICAL_REFERENCE.read_bytes() + b"\n# edited\n")
    review.HISTORICAL_REFERENCE = copy
    with pytest.raises(ValueError, match="historical first-decision reference changed"):
        review.historical_reference()


def test_live_reference_differs_from_the_baseline_only_in_the_cooling_pin():
    review = load_review()
    old = review.historical_reference().PINS
    new = load_path("live_first_decision_reference", HERE.parent / "first_decision" / "reference.py").PINS
    assert {k for k in old if old[k] != new[k]} == {"cooling-pue-ladder"}
    assert new["cooling-pue-ladder"] == "538314a785e8676a63b4a37aa4b9f9c22382ab93"
    assert set(old) == set(new)


def test_review_script_loads_its_baseline_through_the_hash_check():
    source = (HERE / "review.py").read_text()
    assert "reference = historical_reference()" in source
    assert 'working / "research/integration/first_decision/reference.py"' not in source


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
