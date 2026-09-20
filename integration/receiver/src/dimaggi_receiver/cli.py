"""Receiver CLI. JSON stdout; explicit read-only collection commands use network."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import sqlite3

from .adapters import evaluate, exercise
from .bindings import policy_input
from .jsonio import dumps, loads, read_file, read_stream
from .report import build_report
from .sources import verify_bundle
from . import infrastructure_cli, operations_cli
from .observation_cli import COMMANDS, add_commands, run as run_observation


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
    add_commands(commands)
    infrastructure_cli.add_commands(commands)
    operations_cli.add_commands(commands)
    args = parser.parse_args(argv)
    try:
        if args.command in operations_cli.COMMANDS:
            result = operations_cli.run(args)
        elif args.command in infrastructure_cli.COMMANDS:
            result = infrastructure_cli.run(args)
        elif args.command in COMMANDS:
            result = run_observation(args)
        elif args.command == "model":
            result = build_report(args.sources, args.case)
        elif args.command == "exercise":
            result = exercise(args.sources)
        elif args.command == "evaluate":
            raw = read_file(args.input) if args.input else read_stream(sys.stdin.buffer)
            result = evaluate(args.sources, loads(raw.decode("utf-8")))
        elif args.command == "verify-sources":
            result = verify_bundle(args.sources)
        else:
            result = policy_input(read_file(args.report), args.request_id)
        print(dumps(result), end="")
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(dumps({"schema_version": "dimaggi-receiver-error/v1", "error": type(exc).__name__,
                     "message": str(exc), "execution_authorized": False, "mutation_request": None}), end="")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
