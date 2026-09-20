"""Installed local observation commands; never an authority or scheduler client."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .journal_import import import_journal
from .jsonio import loads, read_file
from .observations import ObservationStore, darwin_ofd_command

COMMANDS = {
    "observations-init", "journal-import", "observations-show", "observations-reconcile",
    "observations-history", "observations-triage", "observations-resolve",
    "kubernetes-import",
}


def add_commands(commands):
    for name in sorted(COMMANDS):
        sub = commands.add_parser(name, help="Local attributed evidence; no execution permission")
        sub.add_argument("--journal", type=Path, required=True)
        if name == "observations-init":
            sub.add_argument("--intent", type=Path, required=True)
        elif name in {"observations-show", "observations-reconcile", "observations-history", "journal-import", "kubernetes-import"}:
            sub.add_argument("--request-id", required=True)
            if name in {"journal-import", "kubernetes-import"}:
                sub.add_argument("--input", type=Path, required=True)
                sub.add_argument("--recorded-at", required=True)
                if name == "journal-import":
                    sub.add_argument("--expected-journal-id", required=True)
                else:
                    sub.add_argument("--expected-collector-id", required=True)
                    sub.add_argument("--expected-identity", type=Path, required=True,
                                     help="Separate declared cluster/namespace/Job identity JSON")
            elif name != "observations-history":
                sub.add_argument("--as-of", required=True)
                sub.add_argument("--freshness", type=Path, required=True,
                                 help="JSON seconds for permission, attempt and workload; no defaults")
        else:
            sub.add_argument("--case-id", required=True)
            sub.add_argument("--actor-id", required=True)
            sub.add_argument("--reason", required=True)
            sub.add_argument("--recorded-at", required=True)
            if name == "observations-triage":
                sub.add_argument("--triage-id", required=True)
                sub.add_argument("--reconciliation-id", required=True)
                sub.add_argument("--disposition", choices=("hold", "escalate", "proposal"), required=True)
            else:
                sub.add_argument("--resolution-id", required=True)
                sub.add_argument("--opening-reconciliation-id", required=True)
                sub.add_argument("--resolving-reconciliation-id", required=True)


def _read(path):
    return read_file(path).decode("utf-8")


def _register(store, intent):
    if type(intent) is not dict or type(intent.get("sources")) is not dict:
        raise ValueError("intent and its sources must be JSON objects")
    for kind, source_id in intent["sources"].items():
        store.register_source(source_id, kind, intent["target_id"])
    store.register_intent(intent)


def run(args):
    if args.command == "observations-init":
        intent = loads(_read(args.intent))
        # Validate the complete declared configuration before creating a file.
        with ObservationStore(":memory:") as check:
            _register(check, intent)
        try:
            import fcntl
        except ImportError as exc:
            raise ValueError("file journals require supported Darwin OFD locking") from exc
        darwin_ofd_command()
        descriptor = os.open(args.journal, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with ObservationStore(args.journal) as store:
            _register(store, intent)
        return {"schema": "dimaggi-observation-init/v1", "request_id": intent["request_id"],
                "execution_authorized": False, "mutation_request": None}

    if not args.journal.is_file():
        raise ValueError("an existing configured observation journal is required")
    # Parse entire external documents before opening the owned journal.
    raw = _read(args.input) if args.command in {"journal-import", "kubernetes-import"} else None
    identity = loads(_read(args.expected_identity)) if args.command == "kubernetes-import" else None
    freshness = loads(_read(args.freshness)) if args.command in {"observations-show", "observations-reconcile"} else None
    with ObservationStore(args.journal, create=False) as store:
        if args.command == "kubernetes-import":
            from .kubernetes_import import import_kubernetes
            return import_kubernetes(store, raw, request_id=args.request_id,
                                     expected_collector_id=args.expected_collector_id,
                                     expected_identity=identity, recorded_at_utc=args.recorded_at)
        if args.command == "journal-import":
            return import_journal(store, raw, request_id=args.request_id,
                                  expected_journal_id=args.expected_journal_id,
                                  recorded_at_utc=args.recorded_at)
        if args.command in {"observations-show", "observations-reconcile"}:
            method = store.project if args.command == "observations-show" else store.reconcile
            return method(args.request_id, as_of_utc=args.as_of, freshness_seconds=freshness)
        if args.command == "observations-history":
            return {"schema": "dimaggi-observation-history/v1", "request_id": args.request_id,
                    "events": store.history(args.request_id), "execution_authorized": False, "mutation_request": None}
        if args.command == "observations-triage":
            added = store.record_triage(triage_id=args.triage_id, reconciliation_id=args.reconciliation_id,
                                       case_id=args.case_id, actor_id=args.actor_id, disposition=args.disposition,
                                       reason=args.reason, recorded_at_utc=args.recorded_at)
            history = store.triage_history(args.case_id)
        else:
            added = store.record_resolution(resolution_id=args.resolution_id,
                                           opening_reconciliation_id=args.opening_reconciliation_id,
                                           resolving_reconciliation_id=args.resolving_reconciliation_id,
                                           case_id=args.case_id, actor_id=args.actor_id, reason=args.reason,
                                           recorded_at_utc=args.recorded_at)
            history = store.resolution_history(args.case_id)
        return {"schema": "dimaggi-observation-note/v1", "case_id": args.case_id,
                "appended": added, "history": history, "execution_authorized": False, "mutation_request": None}
