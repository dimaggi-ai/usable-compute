# Maggie model demonstration

This runner reuses the six selected first-decision seed-zero variants at the audited
pins and the new domain references in their owning working repositories. It
generates inspectable Markdown, raw JSON, a claim record and source hashes.
It does not run a batch workload, call an API, collect observations or publish.

From the DIMAGGI AI workspace root:

```sh
projects/infrastructure/_Audits/2026-09-17-repo-aware-strategy/.venv/bin/python \
  projects/infrastructure/DIMAGGI-Infrastructure-Intelligence/research/integration/maggie_demo/demo.py \
  --workspace-root "$PWD" --out /tmp/maggie-demo-new-output
```

Use a new or empty output directory. The runner refuses to overwrite previous
evidence. Dependencies are those of the existing first-decision reference; no
installation or network access is needed in the recorded audit environment.
Ronnie owns portable dependency installation. Pinned model imports are checked
against their audited paths; new references execute in isolated child processes
so working and pinned `capacity` packages do not contaminate each other's imports.

Read the generated `Demo.md` alongside `claims.json` and `provenance.json`.
The story is refusal → binding constraints → negative resource/calibration cases
→ distinct checkpoint phases → remaining executor/operator gates. This is a
runnable local demonstration and storyboard; it is not a recorded video or an
executor proof. All acceptance examples are exposed, never a protected holdout.

Historical run evidence is retained in the internal strategy workspace. For the
portable application and its reproducible installation, see the
[offline receiver guide](../receiver/README.md).

For tests from another checkout layout, set `MAGGIE_WORKSPACE_ROOT` to the
workspace containing the documented audit and owning repositories. The fixture
data remains external to this repository and must exist at the declared revisions.
