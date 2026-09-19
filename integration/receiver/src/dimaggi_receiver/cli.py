"""Offline receiver command line. JSON stdout; no scheduler or network client."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .adapters import evaluate, exercise
from .bindings import policy_input
from .jsonio import dumps, loads
from .report import build_report
from .sources import verify_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("model", "exercise", "evaluate", "verify-sources"):
        sub = commands.add_parser(command)
        sub.add_argument("--sources", type=Path, required=True)
        if command == "model":
            sub.add_argument("--case", default="baseline")
        elif command == "evaluate":
            sub.add_argument("--input", type=Path, help="strict JSON owner input; omitted reads stdin")
    sub = commands.add_parser("policy-input")
    sub.add_argument("--report", required=True, type=Path)
    sub.add_argument("--request-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "model":
            result = build_report(args.sources, args.case)
        elif args.command == "exercise":
            result = exercise(args.sources)
        elif args.command == "evaluate":
            result = evaluate(args.sources, loads(args.input.read_text() if args.input else sys.stdin.read()))
        elif args.command == "verify-sources":
            result = verify_bundle(args.sources)
        else:
            result = policy_input(args.report.read_bytes(), args.request_id)
        print(dumps(result), end="")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(dumps({"schema_version": "dimaggi-receiver-error/v1", "error": type(exc).__name__,
                     "message": str(exc), "execution_authorized": False, "mutation_request": None}), end="")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
