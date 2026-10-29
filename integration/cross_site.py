"""Cross-site placement bound to path evidence and reservations (A1, increment 1).

Existing packages own each decision: compute-power-placement's ledger prices
stay/stitch/move, slicepacker places the slice in each site, and span-contract
admits the crossing against the path's current topology revision. This module
turns the chosen option into reservation claims, reserves them with an owner,
revalidates before the effect and requires an independent outcome.

All inputs are synthetic. One accelerator is mapped to one torus chip; that is
an explicit scenario assumption, not a physical fabric mapping. The owners are
in-process simulations (see path_binding.py).
"""
import argparse
import dataclasses
import json
import math
from pathlib import Path
import sys

REPOS = Path(__file__).resolve().parents[2]
for name in ('compute-power-placement', 'scheduler-vs-more-gpus', 'slice-packer-torus', 'span-contract'):
    sys.path.insert(0, str(REPOS/name)); sys.path.insert(0, str(REPOS/name/'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from span.stay_stitch_move import Hall, Circuit, SpanJob, ledger, pipeline_cut
from slicepacker import Fabric, Topology, Request
from capacity.placement import placement_preview, admission_preview
from spancontract import Plant, SpanEnvelope
import path_binding as pb

ENVELOPE = REPOS/'span-contract/examples/campus-stitch-pipeline.json'
GRAPH_HASH = 'a3f1' + '0'*60
NOT_CHECKED = (
    'Burst or per-step peak path demand: the working-path claim is the mean cut rate',
    'Transition time in ledger durations: see packet C1 (stay/stitch deadline timing)',
    'Communication model cross-check: netcap and the ledger stitch tax are different models',
    'Storage path, NIC and switch-port capacity: no owner supplies them in this scenario',
    'Real reservation owner: SimulatedLeaseOwner protects nothing outside this process',
)


@dataclasses.dataclass(frozen=True)
class Site:
    site_id: str
    shape: tuple
    free_gpus: int
    power_kw: int
    price_mwh: float = 60.0
    spike_price_mwh: float = 0.0
    spike_hours_per_week: float = 0.0

    def hall(self):
        return Hall(self.site_id, self.free_gpus, self.price_mwh, self.spike_price_mwh, self.spike_hours_per_week)


@dataclasses.dataclass(frozen=True)
class CrossSiteRequest:
    job_id: str
    job: SpanJob
    home: str
    away: str
    working_path: str
    protection_path: str = None
    protection_required: bool = False
    activation_gb_per_step: float = 4.0
    objective: float = None                     # minimum pb.METRIC the outcome must reach


def site_fabric(site):
    """Free capacity becomes free chips; the rest is marked allocated by others."""
    fabric = Fabric(Topology(site.shape, (True,)*len(site.shape)))
    taken = math.prod(site.shape) - site.free_gpus
    if taken < 0:
        raise ValueError(f'{site.site_id} reports more free accelerators than its fabric holds')
    if taken:
        fabric.place(Request(f'{site.site_id}-occupied', taken))
    return fabric


def geometry(site, job_id, gpus):
    if gpus == 0:
        return {'status': 'not-needed'}
    return placement_preview(site_fabric(site), [Request(job_id, gpus)])


def span_admission(job_id, rect, path, power_headroom_kw, now, current_revision):
    """Span-contract verdict for a crossing over `path`, checked against the plant's current revision."""
    if path.observed_at > now:
        return {'passes_supplied_checks': False, 'decision': 'deny',
                'reasons': [f'path {path.path_id} observation is dated {path.observed_at}, after the check time {now}'],
                'not_checked': []}
    if path.rtt_us is None or path.capacity_bytes_per_s is None or path.insertion_loss_db is None or path.bit_error_rate is None:
        return {'passes_supplied_checks': False, 'decision': 'deny',
                'reasons': [f'path {path.path_id} lacks delay, capacity or optical-margin evidence'], 'not_checked': []}
    template = json.loads(ENVELOPE.read_text())
    template.update(span_rtt_us=float(path.rtt_us), span_bw_gbps=path.capacity_bytes_per_s*8/1e9,
                    span_failure_domain=','.join(path.srlgs) if path.srlgs else 'unknown',
                    slice_rect={'hall_id': rect['site'], 'origin': rect['origin'], 'extent': rect['extent']},
                    stitch_id=path.path_id, topology_hash=path.topology_revision,
                    measured_il_db=float(path.insertion_loss_db), measured_ber=float(path.bit_error_rate),
                    power_headroom_kw=float(power_headroom_kw), measured_age_s=float(now - path.observed_at))
    template['labels'] = dict(template['labels'], graph_hash=GRAPH_HASH, job=job_id)
    envelope = SpanEnvelope.from_dict(template)
    envelope = dataclasses.replace(envelope, compile_cache_key=envelope.expected_compile_cache_key(GRAPH_HASH))
    return admission_preview(envelope, plant=Plant(current_topology_hash=current_revision))


def plan(request, sites, observations, now):
    """Choose the cheapest ledger option whose geometry, admission and paths all hold.

    Rejected options keep their reasons. If none holds, the result is a refusal,
    not a downgraded placement.
    """
    job, home, away = request.job, sites[request.home], sites[request.away]
    paths = {o.path_id: o for o in observations}
    working = paths.get(request.working_path)
    protection = paths.get(request.protection_path) if request.protection_path else None
    gbps = working.capacity_bytes_per_s*8/1e9 if working and working.capacity_bytes_per_s else 1e-9
    rtt_ms = working.rtt_us/1000 if working and working.rtt_us is not None else 1e9
    cut = pipeline_cut(job, request.activation_gb_per_step)
    options = ledger(job, home.hall(), away.hall(), Circuit(rtt_ms=rtt_ms, gbps=gbps), cut)
    kw = lambda gpus: math.ceil(gpus*job.kw_per_gpu)
    # Without path evidence the ledger prices stitch and move on a placeholder circuit; report the gap instead.
    if working is None:
        path_gap = f'path {request.working_path} has no observation'
    elif set(working.sites) != {home.site_id, away.site_id}:
        path_gap = f"path {working.path_id} joins {'/'.join(working.sites)}, not {home.site_id}/{away.site_id}"
    elif working.observed_at > now:
        path_gap = f'path {working.path_id} observation is dated {working.observed_at}, after the planning time {now}'
    elif now >= working.expires_at:
        path_gap = f'path {working.path_id} observation expired'
    elif working.capacity_bytes_per_s is None or working.rtt_us is None:
        path_gap = f'path {working.path_id} capacity is unknown' if working.capacity_bytes_per_s is None \
            else f'path {working.path_id} delay is unknown'
    else:
        path_gap = None
    evaluated = []
    for option in sorted((o for o in options['options'] if o['option'] in ('stay', 'stitch', 'move')),
                         key=lambda o: (not o['feasible'], o['dollars'] or 0, o['option'])):
        reasons, claims, evidence, domain = [], [], [], {'ledger': option}
        if option['option'] in ('stitch', 'move') and path_gap:
            evaluated.append((option['option'], [path_gap], None)); continue
        if not option['feasible']:
            evaluated.append((option['option'], ['ledger: ' + option['note']], None)); continue
        if option['option'] == 'stay':
            g_home, g_away = min(job.gpus_needed, home.free_gpus), 0
        elif option['option'] == 'move':
            g_home, g_away = 0, job.gpus_needed
        else:
            g_away = max(0, job.gpus_needed - home.free_gpus) or job.gpus_needed//2
            g_home = job.gpus_needed - g_away
        for site, gpus in ((home, g_home), (away, g_away)):
            preview = geometry(site, request.job_id, gpus)
            domain[f'geometry:{site.site_id}'] = preview
            if gpus and preview['requests'][0]['status'] != 'placeable':
                reasons.append(f'geometry: no {gpus}-accelerator slice in {site.site_id}')
            if gpus:
                claims += [pb.Claim(site.site_id, 'accelerators', gpus, 'accelerator', 'compute'),
                           pb.Claim(site.site_id, 'power', kw(gpus), 'kW', 'power')]
                if kw(gpus) > site.power_kw:
                    reasons.append(f'power: {kw(gpus)} kW exceeds {site.site_id} envelope {site.power_kw} kW')
        if option['option'] in ('stitch', 'move'):
            evidence.append(working.ref())
            if option['option'] == 'stitch':
                demand = math.ceil(cut.gb_over_cut_per_step*1e9/job.step_time_s)
                role = 'working-path'
                home_rect = domain[f'geometry:{home.site_id}']['requests'][0]
                verdict = span_admission(request.job_id, dict(home_rect, site=home.site_id), working,
                                         home.power_kw - kw(g_home), now, working.topology_revision)
                domain['admission'] = verdict
                reasons += admission_refusals(verdict)
            else:
                demand = math.ceil(job.state_gb*1e9/(job.migration_downtime_h*3600))
                role = 'transfer-path'
            if demand > working.capacity_bytes_per_s:
                reasons.append(f'path {working.path_id}: {demand} B/s exceeds observed capacity')
            claims.append(pb.Claim('transport', working.path_id, demand, 'bytes/s', role, working.topology_revision))
            if option['option'] == 'stitch' and request.protection_required:
                relation = pb.diversity(working, protection) if protection else 'unknown'
                domain['protection_diversity'] = relation
                if protection is None or relation != 'disjoint':
                    reasons.append(f'protection: no path proven disjoint from {working.path_id} ({relation})')
                elif set(protection.sites) != set(working.sites):
                    reasons.append(f"protection: path {protection.path_id} joins {'/'.join(protection.sites)}, "
                                   f"not the working endpoints")
                elif protection.observed_at > now:
                    reasons.append(f'protection: path {protection.path_id} observation is dated '
                                   f'{protection.observed_at}, after the planning time {now}')
                elif protection.capacity_bytes_per_s is None or now >= protection.expires_at:
                    reasons.append(f'protection: path {protection.path_id} lacks fresh capacity evidence')
                elif demand > protection.capacity_bytes_per_s:
                    reasons.append(f'protection: path {protection.path_id} cannot carry {demand} B/s')
                else:
                    # The fallback must pass the same admission as the working path, or it protects nothing.
                    fallback = span_admission(request.job_id, dict(home_rect, site=home.site_id), protection,
                                              home.power_kw - kw(g_home), now, protection.topology_revision)
                    domain['protection_admission'] = fallback
                    refusals = admission_refusals(fallback)
                    reasons += ['protection ' + r for r in refusals]
                    if not refusals:
                        evidence.append(protection.ref())
                        claims.append(pb.Claim('transport', protection.path_id, demand, 'bytes/s',
                                               'protection-path', protection.topology_revision))
        evaluated.append((option['option'], reasons, (claims, evidence, domain)))
    rejected = {name: reasons for name, reasons, _ in evaluated if reasons}
    chosen = next(((name, body) for name, reasons, body in evaluated if not reasons), None)
    base = dict(job_id=request.job_id, grants=(), unknown=(), objective=request.objective)
    if chosen is None:
        return pb.Plan(plan_id=pb.digest(['refused', now, rejected]), option='refuse', stage='modeled-feasible',
                       status='refused', claims=(), evidence=(), valid_until=now,
                       domain={'rejected': rejected}, reasons=tuple(f'{k}: {r}' for k, v in rejected.items() for r in v),
                       not_checked=NOT_CHECKED, **base)
    name, (claims, evidence, domain) = chosen
    domain['rejected'] = rejected
    domain['home'] = home.site_id
    valid_until = min([e['expires_at'] for e in evidence] or [now + 300])
    plan_id = pb.digest([request.job_id, name, [dataclasses.asdict(c) for c in claims], evidence, now])
    gaps = tuple('span: ' + g for g in domain.get('admission', {}).get('not_checked', ()))
    return pb.Plan(plan_id=plan_id, option=name, stage='admissible', status='ok', claims=tuple(claims),
                   evidence=tuple(evidence), valid_until=valid_until, domain=domain,
                   not_checked=NOT_CHECKED + gaps, **base)


def admission_refusals(verdict):
    """Refuse unless span-contract says SPAN with no findings.

    Checks span could not run (its not_checked list) do not pass silently: the
    plan carries them in its own not_checked list for the reviewer to see.
    """
    if verdict['decision'] == 'span' and not verdict['reasons']:
        return []
    return ['admission: ' + r for r in verdict['reasons'] or [f"span decision {verdict['decision']}"]]


def admission_check(sites):
    """Admission callback for revalidate(): rerun span against the plant's current revision.

    Both the working and the protection path are rechecked. Everything it needs
    comes from the plan itself, so a re-plan on another path is checked against
    that path. Evidence age is measured from the older of the planned and the
    current observation time, so neither a refreshed nor a back-dated
    observation makes the evidence look younger.
    """
    def check(plan, latest, now):
        if plan.option != 'stitch':
            return None
        home = sites[plan.domain['home']]
        home_kw = next(c.amount for c in plan.claims if c.owner == home.site_id and c.resource == 'power')
        rect = dict(plan.domain[f'geometry:{home.site_id}']['requests'][0], site=home.site_id)
        reasons = []
        for claim in plan.claims:
            if claim.role not in ('working-path', 'protection-path'):
                continue
            planned = next(e for e in plan.evidence if e['path_id'] == claim.resource)
            current = latest[claim.resource]
            replayed = dataclasses.replace(current, topology_revision=planned['topology_revision'],
                                           observed_at=min(planned['observed_at'], current.observed_at))
            verdict = span_admission(plan.job_id, rect, replayed, home.power_kw - home_kw, now,
                                     latest[claim.resource].topology_revision)
            refused = verdict['reasons'] or [f"span decision {verdict['decision']}"]
            if admission_refusals(verdict):
                prefix = '' if claim.role == 'working-path' else f'protection {claim.resource}: '
                reasons += [prefix + r for r in refused]
        return dict(reasons=reasons, passes_supplied_checks=not reasons)
    return check


def owners_for(sites, observations):
    owners = {s.site_id: pb.SimulatedLeaseOwner(s.site_id, {'accelerators': s.free_gpus, 'power': s.power_kw})
              for s in sites.values()}
    owners['transport'] = pb.SimulatedLeaseOwner(
        'transport', {o.path_id: o.capacity_bytes_per_s or 0 for o in observations},
        {o.path_id: o.topology_revision for o in observations})
    return owners


def rev(label):
    return pb.digest(label)


def scenario():
    """Synthetic three-site federation; C is partly ready and its path diversity is unknown."""
    sites = {s.site_id: s for s in (
        Site('site-a', (8, 8, 16), free_gpus=512, power_kw=900, spike_price_mwh=400, spike_hours_per_week=40),
        Site('site-b', (8, 8, 16), free_gpus=640, power_kw=1000),
        Site('site-c', (8, 8, 8), free_gpus=256, power_kw=400))}
    common = dict(source='synthetic-path-collector', source_epoch=1, observed_at=1000, expires_at=1600,
                  evidence_class='synthetic', insertion_loss_db=6.0, bit_error_rate=1e-12)
    m = pb.Member
    observations = (
        pb.PathObservation('ab-1', ('site-a', 'site-b'), rev('ab-1/r1'), capacity_bytes_per_s=50_000_000_000,
                           rtt_us=42, srlgs=('duct-east',), members=(m('dcim', 'duct', 'east'), m('ols', 'och', 'ab-1')),
                           **common),
        pb.PathObservation('ab-2', ('site-a', 'site-b'), rev('ab-2/r1'), capacity_bytes_per_s=50_000_000_000,
                           rtt_us=55, srlgs=('duct-west',), members=(m('dcim', 'duct', 'west'), m('ols', 'och', 'ab-2')),
                           **common),
        pb.PathObservation('ac-1', ('site-a', 'site-c'), rev('ac-1/r1'), capacity_bytes_per_s=25_000_000_000,
                           rtt_us=60, srlgs=None, members=(m('carrier-x', 'circuit', 'c-77'),), **common),
    )
    # A deadline the shrunk stay option misses; B cannot hold the whole job, so move is infeasible.
    job = SpanJob(params_b=70.0, gpus_needed=1024, hours_at_full=48.0, step_time_s=5.0, kw_per_gpu=1.4,
                  deadline_h=60.0)
    return sites, observations, job


def summarize(name, plan):
    return dict(step=name, option=plan.option, stage=plan.stage, status=plan.status, reasons=list(plan.reasons),
                claims=[dataclasses.asdict(c) for c in plan.claims])


def experiment():
    """Admit, reserve, contend, dispatch, verify; then degrade the working path and re-plan."""
    sites, observations, job = scenario()
    request = CrossSiteRequest('train-70b', job, 'site-a', 'site-b', 'ab-1', 'ab-2', protection_required=True,
                               objective=0.9)
    owners = owners_for(sites, observations)
    admission = admission_check(sites)
    executions = pb.EffectLedger()
    effects, steps = [], []

    first = plan(request, sites, observations, now=1100)
    steps.append(summarize('plan', first))
    held = pb.reserve(first, owners, observations, now=1110, admission=admission)
    steps.append(summarize('reserve', held))
    rival = plan(dataclasses.replace(request, job_id='rival'), sites, observations, now=1115)
    steps.append(summarize('rival-reserve', pb.reserve(rival, owners, observations, now=1115, admission=admission)))
    done = pb.dispatch(held, owners, observations, now=1120, effect=effects.append, executions=executions,
                       admission=admission)
    steps.append(summarize('dispatch', done))
    # Replaying the reserved snapshot must not run the effect a second time.
    steps.append(summarize('replay-reserved-snapshot', pb.dispatch(held, owners, observations, now=1121,
                           effect=effects.append, executions=executions, admission=admission)))
    steps.append(summarize('verify', pb.verify(done, dict(source='synthetic-progress-observer', observed_at=1400,
                                                         job_id='train-70b', metric=pb.METRIC, achieved=0.93))))

    # The working path degrades: the collector publishes a new revision with far less capacity.
    degraded = dataclasses.replace(observations[0], topology_revision=rev('ab-1/r2'), source_epoch=2,
                                   observed_at=1450, expires_at=2000, capacity_bytes_per_s=500_000_000)
    later = (degraded,) + tuple(dataclasses.replace(o, observed_at=1450, expires_at=2000) for o in observations[1:])
    owners['transport'].publish_revision('ab-1', degraded.topology_revision)
    reasons = pb.revalidate(done, later, 1460, admission)
    steps.append(summarize('revalidate-after-degradation', pb.advance(done, status='invalidated', reasons=reasons)))
    try:
        pb.dispatch(done, owners, later, 1460, effects.append, executions=executions, admission=admission)
    except ValueError as exc:
        steps.append(dict(step='second-dispatch-refused', reasons=[str(exc)]))
    for grant in done.grants:
        owners[grant.owner].release(grant.key)

    swapped = dataclasses.replace(request, working_path='ab-2', protection_path='ab-1')
    steps.append(summarize('replan-protect-on-degraded-path', plan(swapped, sites, later, now=1470)))
    via_c = dataclasses.replace(request, working_path='ab-2', protection_path='ac-1')
    steps.append(summarize('replan-protect-via-unknown-diversity', plan(via_c, sites, later, now=1470)))
    unprotected = dataclasses.replace(via_c, protection_required=False)
    final = plan(unprotected, sites, later, now=1470)
    steps.append(summarize('replan-without-protection', final))
    steps.append(summarize('reserve-without-protection', pb.reserve(final, owners, later, now=1475, admission=admission)))

    return {'schema': pb.SCHEMA, 'evidence_class': 'synthetic', 'steps': steps, 'effects_run': len(effects),
            'not_checked': list(final.not_checked),
            'limits': 'Simulated owners and synthetic evidence; no controller, scheduler or device was contacted.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', type=Path, help='write the result JSON here')
    args = parser.parse_args()
    text = json.dumps(experiment(), indent=2, sort_keys=True) + '\n'
    (args.out.write_text(text) if args.out else sys.stdout.write(text))


if __name__ == '__main__':
    main()
