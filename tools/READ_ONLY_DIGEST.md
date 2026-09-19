# Two-repository digest preview

`read_only_digest.py` consumes the approved scheduler/span profile selection. It
does not repeat portfolio discovery. The existing `portfolio_snapshot.py` remains
the membership-discovery tool; this collector uses its already reviewed result.

Create a local JSON map containing exactly `scheduler-vs-more-gpus` and
`span-contract`, with each value pointing to an existing Git checkout. Keep local
paths and private working-state details out of published artifacts.

```sh
python3 tools/read_only_digest.py --source-map /path/to/two-repo-map.json \
  --out /path/to/new-digest-preview.json
python3 -m pytest tools/test_read_only_digest.py -q
```

The command reads committed blobs and hashes, compares the approved baseline
ancestor with local HEAD, and records dirty/untracked status. It disables Git's
optional index writes, replacement objects and filesystem-monitor hook. It does
not fetch, execute repository code, run tests, interpret source instructions,
modify repositories or assign domain dispositions. Unavailable sources make the
output incomplete. An existing output file is preserved rather than overwritten.

The result is a preview, not a completed weekly digest. Before using it to guide a
decision, record the matched manual/CI task baseline, human review time, setup and
ongoing effort, cost, exposure/order effects and owner dispositions. These values
remain null in this collector. Previously completed product work and exposed
fixtures are not newly discovered RSI improvements.

Remote changes and CI results remain not checked. No change between local commits
does not establish no upstream change. The four-week usefulness gate remains
unmet until its actual observations exist. No candidate execution, promotion,
new repository or recommendation service is implemented.
