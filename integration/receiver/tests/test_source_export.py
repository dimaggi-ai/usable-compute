import hashlib
import importlib.util
from pathlib import Path
import subprocess

import pytest


spec = importlib.util.spec_from_file_location("source_export", Path(__file__).parents[1] / "tools/export_sources.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL, text=True).strip()
    git("init", "-q")
    (repo / "model.py").write_text("value = 42\n")
    git("add", "model.py")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")
    lock = {"schema_version": "dimaggi-receiver-sources/v1", "repositories": {"model": {
        "commit": git("rev-parse", "HEAD"), "files_sha256": {"model.py": hashlib.sha256((repo / "model.py").read_bytes()).hexdigest()}}}}
    return repo, lock


def test_export_uses_committed_bytes_preserves_working_edit(source, tmp_path):
    repo, lock = source
    (repo / "model.py").write_text("user change\n")
    output = tmp_path / "export"
    exporter.export(lock, {"model": str(repo)}, output)
    assert (output / "model/model.py").read_text() == "value = 42\n"
    assert (repo / "model.py").read_text() == "user change\n"
    with pytest.raises(ValueError, match="already exists"):
        exporter.export(lock, {"model": str(repo)}, output)


def test_bad_hash_never_publishes_partial_bundle(source, tmp_path):
    repo, lock = source
    lock["repositories"]["model"]["files_sha256"]["model.py"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        exporter.export(lock, {"model": str(repo)}, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("path", ["../escape.py", "/absolute", "a/../b", "a//b", "a/./b", "a\\b", ""])
def test_unsafe_lock_path_rejected(path):
    with pytest.raises(ValueError):
        exporter.relative_path(path)


def test_source_map_must_be_complete(source, tmp_path):
    _, lock = source
    with pytest.raises(ValueError, match="exactly"):
        exporter.export(lock, {}, tmp_path / "export")
