# Joined usable-capacity experiment

Run from the usable-compute repository with the sibling repositories present:

```
python integration/usable_capacity.py
python -m pytest integration -q
```

Required Python dependencies are NumPy and the dependencies of netcap (including
PyYAML). The script imports the existing cooling admission model, the scheduler
capacity preview, slicepacker's allocator, netcap's healthy-step model, and the
reliability-economics event simulator. It does not copy their algorithms.

This is a static 512-accelerator, 30-day scenario with simultaneous 128-chip
gang requests. A cooling/power admission decision precedes real rectangular
placement. Recovery accounts for checkpoint, restart and discarded time.
Only the retained runtime is split using the healthy-step compute/communication
mix; netcap's full reliability ledger is deliberately not multiplied into it.
All buckets conserve nominal accelerator-hours.

The degraded baseline deliberately lacks any 128-chip rectangle despite free
chips. It is a counterexample to count-only admission, not representative
fleet fragmentation. More power or bandwidth alone cannot fix that baseline;
the combined geometry/network intervention reveals an interaction. Reported
intervention differences are not ROI: intervention costs were not established.

Limits: one local hall, no span admission, no live scheduling loop, no realistic
H100-to-torus calibration, no completion-time or training-quality prediction.
The constant healthy-step mix is an explicit composition assumption, not a
universal multiplicative capacity identity. Do not generalize the ranking
beyond the counterexample. Broader trace-driven integration remains necessary.
# Closure scenario matrix

From the usable-compute repository root, with the same sibling repositories and
dependencies as the original joined experiment:

```sh
python integration/scenario_matrix.py
python -m pytest integration -q
```

`scenario-matrix.json` retains all six predefined cases, four common seeds,
four single interventions and all six pair interventions: 264 model runs.
The cases contrast power, geometry, gang/sequence shape, network degradation
and failure intensity. Rankings are useful GPU-hours recovered, not monetary
returns. No scenarios are excluded for producing zero benefit. Pair interactions
are joint delta minus both individual deltas under the same baseline and seed.

This remains a static-window synthetic experiment. Geometry is not calibrated
to a particular GPU interconnect; changing gang size changes pipeline degree.
No job completion, convergence, or population-wide investment ranking is claimed.

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
