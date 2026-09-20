"""Verify exported source bytes before loading any owning-domain code.

The installed lock is the receiver's reviewed source selection. Request data
cannot supply another lock, weaker profile, checkout or import root. Hashes are
local integrity records; they are not authenticated operator evidence.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform

from .jsonio import digest

LOCK_PATH = Path(__file__).with_name("sources.lock.json")


def source_lock():
    return json.loads(LOCK_PATH.read_text())


def verify_bundle(root):
    root = Path(root).resolve(strict=True)
    lock = source_lock()
    repositories = {}
    for name, spec in lock["repositories"].items():
        repo = root / name
        if not repo.is_dir() or repo.is_symlink():
            raise ValueError(f"missing or symlinked source repository: {name}")
        for relative, expected in spec["files_sha256"].items():
            path = repo / relative
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(repo):
                raise ValueError(f"missing or symlinked source file: {name}/{relative}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"source digest mismatch: {name}/{relative}")
        # Exported bundles contain only the lock. Reject .pyc/native extensions,
        # symlinked directories and other additions that could shadow imports.
        unexpected = sorted(str(path.relative_to(repo)) for path in repo.rglob("*")
                            if path.is_symlink() or (path.is_file()
                            and str(path.relative_to(repo)) not in spec["files_sha256"]))
        if unexpected:
            raise ValueError(f"unexpected source file in {name}: {unexpected[0]}")
        repositories[name] = {"commit": spec["commit"],
                              "files_digest": digest(spec["files_sha256"]),
                              "file_count": len(spec["files_sha256"])}
    return {"source_lock_digest": digest(lock), "repositories": repositories,
            "runtime": {"python": platform.python_version(),
                        **{name: metadata.version(name) for name in ("numpy", "PyYAML", "jsonschema")}},
            "integrity": "verified local bytes; not source authenticity or operator truth"}


def imported_origin(root, name, module):
    path = Path(module.__file__).resolve()
    relative = path.relative_to(root)
    parts = relative.parts
    lock = source_lock()
    expected = lock["repositories"][parts[0]]["files_sha256"].get(str(Path(*parts[1:])))
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected != actual:
        raise ValueError(f"unlocked imported source: {name}")
    return {"module": name, "path": str(relative), "sha256": actual}
