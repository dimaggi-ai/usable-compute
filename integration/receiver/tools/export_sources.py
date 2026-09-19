"""Export reviewed Git objects into a portable source bundle; never edit a repo."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("source names must be nonempty relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("unsafe source path")
    return value


def export(lock: dict, source_map: dict, destination: Path) -> dict:
    if lock.get("schema_version") != "dimaggi-receiver-sources/v1":
        raise ValueError("unsupported source lock")
    repositories = lock["repositories"]
    if not repositories or set(repositories) != set(source_map):
        raise ValueError("source map must name exactly the locked repositories")
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("output already exists; choose a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": "dimaggi-source-export/v1", "repositories": {}}
    with tempfile.TemporaryDirectory(prefix=".receiver-export-", dir=destination.parent) as staging:
        stage = Path(staging) / "sources"
        stage.mkdir()
        for name, record in repositories.items():
            relative_path(name)
            if "/" in name or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                raise ValueError("repository names must be one simple directory")
            commit = record["commit"]
            if not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError("source revision must be an exact Git commit")
            files = record["files_sha256"]
            if not files:
                raise ValueError("empty source lock")
            for path, digest in files.items():
                relative_path(path)
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("invalid source digest")
            repo = Path(source_map[name]).resolve()
            # Read committed objects, so an unrelated working edit cannot enter
            # the bundle and exporting does not require resetting any checkout.
            resolved = subprocess.check_output(
                ["git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
                 "-C", str(repo), "rev-parse", commit + "^{commit}"], text=True
            ).strip()
            if resolved != commit:
                raise ValueError("source commit mismatch")
            archive = subprocess.check_output(
                ["git", "--no-optional-locks", "--no-replace-objects", "-c", "core.fsmonitor=false",
                 "-C", str(repo), "--literal-pathspecs", "archive", "--format=tar", commit, "--", *sorted(files)]
            )
            seen = set()
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
                for member in tar:
                    if member.isdir():
                        continue
                    if not member.isfile() or member.name not in files or member.name in seen:
                        raise ValueError("archive contains an unexpected file or symlink")
                    content = tar.extractfile(member).read()
                    if hashlib.sha256(content).hexdigest() != files[member.name]:
                        raise ValueError("source hash mismatch: " + name + "/" + member.name)
                    target = stage / name / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    seen.add(member.name)
            if seen != set(files):
                raise ValueError("archive omitted a locked file")
            manifest["repositories"][name] = {"commit": commit, "files": len(seen)}
        (stage / "export-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if destination.exists():
            raise ValueError("output appeared during export")
        stage.rename(destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).resolve().parents[1] / "src/dimaggi_receiver/sources.lock.json")
    parser.add_argument("--source-map", type=Path, required=True, help="JSON mapping logical repo names to local Git checkouts")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = export(json.loads(args.lock.read_text()), json.loads(args.source_map.read_text()), args.out)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
