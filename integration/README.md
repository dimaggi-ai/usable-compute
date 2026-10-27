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
