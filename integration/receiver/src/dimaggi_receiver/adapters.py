"""Subprocess transport to source-locked owner implementations."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from .jsonio import dumps, loads


def call_owner(sources, operation, arguments):
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTHONHOME")}
    process = subprocess.run([sys.executable, "-I", "-B", "-m", "dimaggi_receiver._worker",
                              str(Path(sources).resolve())],
                             input=dumps({"operation": operation, "input": arguments}),
                             text=True, capture_output=True, check=False, env=env, timeout=120)
    try:
        document = loads(process.stdout)
    except ValueError as exc:
        raise ValueError("owner worker did not produce a valid JSON result") from exc
    if process.returncode:
        reason = document.get("error", {}).get("message", "worker failed")
        raise ValueError(f"owner worker refused input/source: {reason}")
    if set(document) != {"result", "sources"}:
        raise ValueError("unexpected owner worker response")
    return document


def evaluate(sources, request):
    envelope = call_owner(sources, "evaluate", request)
    return {"schema_version": "dimaggi-domain-adapter/v1", "evidence_class": "offline_derived_check",
            **envelope, "execution_readiness": "incomplete", "execution_authorized": False,
            "mutation_request": None,
            "meaning": "Supplied offline input evaluated by locked owner code; no authenticated observation or permission."}


def exercise(sources):
    return {"schema_version": "dimaggi-receiver-cases/v1", **call_owner(sources, "exercise", {}),
            "execution_authorized": False, "mutation_request": None}
