# Cross-site path binding (Build A1)

From the usable-compute repository root, with compute-power-placement,
scheduler-vs-more-gpus, slice-packer-torus and span-contract checked out beside
it (standard library only):

```sh
python integration/cross_site.py --out integration/path-binding-results.json
python -m unittest discover -s integration -p 'test_path_binding.py'
```

`path_binding.py` is a small contract (`dimaggi-path-binding/v1`) that carries
one cross-site plan through modeled-feasible, admissible, reserved, dispatched
and verified. Each stage records the evidence it rests on, its expiry and what
was not checked. `cross_site.py` builds the plan from the existing components
rather than new ones: the stay/stitch/move ledger chooses the option, the
scheduler preview places the slices, span-contract admits the crossing against
the plant's current topology revision, and observed path evidence supplies
capacity, delay and SRLG membership.

What the tests establish on synthetic evidence:

- concurrent plans cannot overbook a path owner, and refused plans report the
  grants they already hold so they can be compensated;
- expired evidence, an expired lease or a changed topology revision blocks
  reservation or dispatch before any effect runs;
- a lost owner reply leaves the plan outcome-unknown until reconciliation, and
  a retry with the same idempotency keys takes no further capacity;
- protection is accepted only when SRLG disjointness is proven by one
  authority; unknown or differently named groups are not assumed disjoint;
- missing capacity, delay or optical-margin evidence refuses rather than
  defaulting;
- verification needs an independent observation after dispatch.

Limits: the lease owners are in-process simulations, the evidence is synthetic,
and nothing contacts a controller, scheduler or device. The working-path claim is
the mean cut rate, not the burst peak. Ledger durations omit stay and stitch
transition time (packet C1). The output lists every gap under `not_checked`.
