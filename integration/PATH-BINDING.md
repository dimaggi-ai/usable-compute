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
- each reservation runs its effect at most once: `EffectLedger` records an
  execution identity (plan id plus the set of grants, each as owner, key and
  fencing token, in canonical order, so a reconciled plan and a retried one
  with the same grants match), so replaying the reserved snapshot, or dispatching it concurrently, returns
  `duplicate`; an effect that ends without a clear result stays
  `effect-unknown` and is not retried until an independent observer resolves it;
- compensation releases held and unknown-outcome keys, then confirms with each
  owner; it reports `compensated` only when no owner still holds a key, and
  `compensation-pending` otherwise;
- a path is usable only between the plan's two sites. Protection counts only
  when SRLG disjointness is proven by one authority (unknown or differently
  named groups are not assumed disjoint), the protection path joins the same
  sites as the working path, and it passes the same span-contract admission,
  both at planning and again before reservation and dispatch. Otherwise the
  plan is refused rather than reported as protected;
- missing capacity, delay or optical-margin evidence refuses rather than
  defaulting;
- revalidation binds each path's source and evidence class, not just its
  revision and epoch; a replacement source invalidates the plan. It also binds a
  digest of the path's endpoints, shared-risk groups and members, so changing
  any of them at the same revision and epoch invalidates the plan;
- verification needs an independent observation after dispatch, measuring
  `goodput-fraction` against the objective fixed in the plan (a value in
  (0, 1]). Booleans, NaN, infinities, negative values and integers beyond
  ±(2**53 - 1) are rejected as out of domain, not converted to float.

Limits: the lease owners and the effect ledger are in-process simulations, the evidence is synthetic,
and nothing contacts a controller, scheduler or device. The working-path claim is
the mean cut rate, not the burst peak. Ledger durations omit stay and stitch
transition time (packet C1). The fencing check and the effect are not one atomic
step here; a real executor must check the fencing token itself. The output lists every gap under `not_checked`.
