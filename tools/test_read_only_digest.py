import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("readonly_digest", Path(__file__).with_name("read_only_digest.py"))
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True, stderr=subprocess.DEVNULL).strip()
    git("init", "-q")
    (tmp_path / "source.py").write_text("raise RuntimeError('must never run')\n")
    git("add", "source.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    return tmp_path, git("rev-parse", "HEAD")


def test_collects_only_committed_bytes_without_running_source(repo):
    root, commit = repo
    row = digest.inspect_snapshot(root, commit)
    assert row["working_tree_clean"]
    assert row["changed_paths"] == []
    assert row["ci_results"] == "not_checked"
    assert row["tests_run_by_collector"] is False
    assert set(row["files_sha256"]) == {"source.py"}


def test_dirty_snapshot_visible_and_not_read_as_head(repo):
    root, commit = repo
    original = digest.inspect_snapshot(root, commit)
    (root / "source.py").write_text("uncommitted change")
    (root / "unknown.txt").write_text("untracked")
    changed = digest.inspect_snapshot(root, commit)
    assert not changed["working_tree_clean"]
    assert changed["files_sha256"] == original["files_sha256"]
    assert "unknown.txt" in changed["working_status"]


def test_status_does_not_run_fsmonitor_or_refresh_index(repo, tmp_path):
    root, commit = repo
    hook = root / "fsmonitor.sh"
    marker = root / "hook-ran"
    hook.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
    hook.chmod(0o700)
    subprocess.run(["git", "-C", str(root), "config", "core.fsmonitor", str(hook)], check=True)
    index = root / ".git/index"
    before = index.read_bytes()
    row = digest.inspect_snapshot(root, commit)
    assert not row["working_tree_clean"]
    assert not marker.exists()
    assert index.read_bytes() == before


def test_cannot_expand_profiles():
    with pytest.raises(ValueError, match="exactly"):
        digest.collect({"third-repo": "."})


def test_missing_repos_are_incomplete_not_unchanged(tmp_path):
    result = digest.collect({name: str(tmp_path / name) for name in digest.PROFILES})
    assert result["status"] == "incomplete"
    assert len(result["errors"]) == 2
    assert all(row["source_state"] == "unavailable" for row in result["repositories"].values())
    assert result["baseline"]["active_review_minutes"] is None
    assert result["constraints"]["candidate_execution"] is False
    assert result["owner_dispositions"] == []


def test_no_fabricated_baseline_or_dispositions(repo, monkeypatch):
    root, commit = repo
    monkeypatch.setattr(digest, "PROFILES", {"a": ("A", commit), "b": ("B", commit)})
    result = digest.collect({"a": str(root), "b": str(root)})
    assert result["status"] == "preview_only"
    assert result["usefulness"] == "not_measured"
    assert result["completed_weekly_digests"] == 0
    assert result["constraints"]["decision_use_before_manual_baseline"] is False
