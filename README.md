# DIMAGGI AI — Usable Compute

**Turn infrastructure into dependable, usable compute.**

DIMAGGI connects workload planning, compute, networks, storage, power and governed
operations through explicit contracts and traceable evidence. The aim is to make
infrastructure decisions account for the whole system: what a workload needs,
where it fits, which changes are authorized, and what actually happened.

The installable [infrastructure receiver](integration/receiver/INFRASTRUCTURE.md)
combines versioned capabilities, exact software compatibility, shared resource
budgets and observed-state reconciliation. Its CPU evidence connects to TENWA's
signed execution configuration. [Provider telemetry and source review](integration/receiver/OPERATIONS.md)
now join the same assessment, with persistent drift findings and replayable failure
campaigns. Training, inference and agentic workloads share
the planning contract; live TPU/GPU execution remains a separate validation step.
See the [delivery assessment](integration/receiver/DELIVERY-ASSESSMENT.md) for the
boundary between implemented software, local lab evidence and remaining proof.

The expiry ledger is append-only by cooperation, not tamper-evident. Whole-line
truncation, valid-file substitution, or re-creation by the reader identity is
undetectable and can erase recorded expiry. Retain its history; a fresh ledger
requires retiring old collector generations and relisting.

Manifest completeness is relative to the committed, reviewed manifest.
Regenerating it after shrinking the test selection blesses that shrink, so
manifest changes need review alongside source and workflow changes. The gate
does not authenticate arbitrary JUnit XML or defend against a repository editor.

Turning AI infrastructure investment into usable compute: a portfolio covering
scheduling, networking, reliability, power, cooling, placement, and governed operations.

See the [Infrastructure Intelligence portfolio assessment](INFRASTRUCTURE_INTELLIGENCE.md)
for implementation ownership, capability mapping and explicit integration boundaries.

For collective reviews, use the [portfolio workflow](PORTFOLIO.md) to discover
membership from GitHub custom properties and capture the exact commits to analyze.

This repository is the entry point to DIMAGGI AI's open, reproducible infrastructure
portfolio. Served via GitHub Pages; it indexes the series and installable standards.
Source: `index.html`.

Live: https://dimaggi-ai.github.io/usable-compute/

[Cover artwork](COVER_ARTWORK.md): visual index, download links and sharing-image setup
for the public portfolio.

[Cover strategy](COVER_STRATEGY.md): clear research copy, restrained illustrations,
and proofreading checks.
