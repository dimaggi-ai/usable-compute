# Acceptance gate

`check_acceptance.py` compares JUnit test identities and suite counters with the
committed receiver or tools manifest. It checks the configured policy marker and
the source-pin-specific expected failure. JUnit XML is unauthenticated: a forged
report can also forge the marker.

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

Run `python integration/receiver/tools/ci_test_manifest.py --check` from the
repository root with the source bundle configured to check collection against
the manifests. Omit `--check` only for an intended collection change, then review
the manifest diff. Tests added for a fix must remain in the workflow selection.
