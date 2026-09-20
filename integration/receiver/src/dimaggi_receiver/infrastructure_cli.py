"""Installed infrastructure contracts; strict local inputs, no network or writes."""

from . import infrastructure as infra
from .jsonio import loads, read_file

COMMANDS = {
    "infrastructure-plan",
    "infrastructure-assess",
    "infrastructure-reconcile",
    "infrastructure-drift",
    "infrastructure-cpu-binding",
}


def add_commands(commands):
    for name in sorted(COMMANDS):
        p = commands.add_parser(name)
        p.add_argument("--registry", required=True)
        p.add_argument("--registry-digest", required=True)
        if name == "infrastructure-drift":
            p.add_argument("--candidate", required=True)
            p.add_argument("--candidate-digest", required=True)
        else:
            p.add_argument("--input", required=True)
            p.add_argument("--as-of", required=True)
        if name == "infrastructure-assess":
            p.add_argument("--evidence", required=True)
            p.add_argument("--evidence-digest", required=True)
        if name in {"infrastructure-reconcile", "infrastructure-cpu-binding"}:
            p.add_argument("--plan", required=True)
        if name == "infrastructure-reconcile":
            p.add_argument("--observations", required=True)
            p.add_argument("--expected-objects", required=True)


def run(a):
    def read(p):
        return loads(read_file(p).decode("utf-8"))

    r = read(a.registry)
    if a.command == "infrastructure-drift":
        return infra.release_diff(
            r, read(a.candidate), a.registry_digest, a.candidate_digest
        )
    request = read(a.input)
    if a.command == "infrastructure-assess":
        from .assessment import assess

        return assess(
            r, request, a.registry_digest, read(a.evidence), a.evidence_digest, a.as_of
        )
    if a.command == "infrastructure-plan":
        return infra.plan(r, request, a.registry_digest, a.as_of)
    if a.command == "infrastructure-cpu-binding":
        return infra.cpu_binding(r, request, a.registry_digest, read(a.plan), a.as_of)
    return infra.reconcile(
        r,
        request,
        a.registry_digest,
        read(a.plan),
        read(a.observations),
        read(a.expected_objects),
        a.as_of,
    )
