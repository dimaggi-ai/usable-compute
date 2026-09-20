"""Reproduce installed receiver ↔ TENWA contracts with retained, hashed results.

Run with an installed receiver environment and reviewed local TENWA checkout.
No cluster, credentials, network clients or GitHub mutations are used.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def verify_installed(receiver):
    import dimaggi_receiver

    installed = Path(dimaggi_receiver.__file__).resolve().parent
    source = receiver / "integration/receiver/src/dimaggi_receiver"
    hashes = {}
    for path in source.iterdir():
        if path.suffix not in {".py", ".json"}:
            continue
        expected = hashlib.sha256(path.read_bytes()).hexdigest()
        if (
            not (installed / path.name).is_file()
            or hashlib.sha256((installed / path.name).read_bytes()).hexdigest()
            != expected
        ):
            raise ValueError("installed receiver differs from checkout: " + path.name)
        hashes[path.name] = expected
    if not hashes:
        raise ValueError("receiver source missing")
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenwa-root", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receiver = Path(__file__).resolve().parents[3]
    tenwa = args.tenwa_root.resolve()
    sources = args.sources.resolve()
    output = args.output.resolve()
    if not (tenwa / "internal/batchexecutor/executor.go").is_file():
        raise ValueError("reviewed TENWA checkout required")
    if importlib.metadata.version("dimaggi-offline-receiver") != "0.1.7":
        raise ValueError("install receiver 0.1.7 first")
    from dimaggi_receiver.sources import verify_bundle

    verify_bundle(sources)
    installed_hashes = verify_installed(receiver)
    if any(output == r or r in output.parents for r in (receiver, tenwa)):
        raise ValueError("verification output must be outside both repositories")
    output.mkdir(parents=True, exist_ok=False)
    (output / "bin").mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(GOWORK="off", GOPROXY="off", GOSUMDB="off")
    records = []
    complete = False

    def run(name, argv, cwd):
        start = time.monotonic()
        with (output / (name + ".txt")).open("wb") as log:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=900,
            )
        records.append(
            dict(
                name=name,
                argv=list(map(str, argv)),
                exit_code=result.returncode,
                elapsed_seconds=time.monotonic() - start,
            )
        )
        if result.returncode:
            raise RuntimeError(name + " failed; see retained log")

    try:
        for name in (
            "batch-job-plan",
            "batch-preview",
            "batch-journal-demo",
            "batch-object-check",
            "batch-cpu-payload",
        ):
            run(
                "build-" + name,
                ["go", "build", "-o", str(output / "bin" / name), "./cmd/" + name],
                tenwa,
            )
        run("go-race", ["go", "test", "-race", "-count=1", "-json", "./..."], tenwa)
        run("go-vet", ["go", "vet", "./..."], tenwa)
        env.update(
            DIMAGGI_TEST_SOURCES=str(sources),
            DIMAGGI_BATCH_OBJECT_FIXTURES=str(
                tenwa / "internal/batchobject/testdata/kubernetes-v1.35.0"
            ),
        )
        for var, name in {
            "DIMAGGI_BATCH_JOB_PLAN": "batch-job-plan",
            "DIMAGGI_BATCH_PREVIEW": "batch-preview",
            "DIMAGGI_BATCH_JOURNAL": "batch-journal-demo",
            "DIMAGGI_BATCH_OBJECT_CHECK": "batch-object-check",
            "DIMAGGI_BATCH_CPU_PAYLOAD": "batch-cpu-payload",
        }.items():
            env[var] = str(output / "bin" / name)
        run(
            "receiver",
            [
                sys.executable,
                "-m",
                "pytest",
                "integration/receiver/tests",
                "-q",
                "--junitxml",
                str(output / "receiver.xml"),
            ],
            receiver,
        )
        suites = list(ET.parse(output / "receiver.xml").getroot().iter("testsuite"))
        if not suites or any(
            int(s.get(k, "0"))
            for s in suites
            for k in ("skipped", "failures", "errors")
        ):
            raise RuntimeError(
                "receiver acceptance requires zero skips, failures and errors"
            )
        run(
            "benchmark",
            [
                sys.executable,
                "integration/receiver/examples/infrastructure/benchmark.py",
            ],
            receiver,
        )
        complete = True
    finally:
        manifest = dict(
            schema="dimaggi-interoperability-run/v1",
            status="complete" if complete else "failed",
            python=sys.version,
            receiver_version=importlib.metadata.version("dimaggi-offline-receiver"),
            commands=records,
            installed_source_hashes=installed_hashes,
            files={
                str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in output.rglob("*")
                if p.is_file()
            },
        )
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(str(output / "manifest.json"))


if __name__ == "__main__":
    main()
