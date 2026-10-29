"""Build A acceptance cases from the 2026-10-26 networking brief, on synthetic evidence.

Runs with the standard library: python3 -m unittest discover -s integration -p 'test_path_binding.py'
"""
import dataclasses
import threading
import unittest

import cross_site as cs
import path_binding as pb


def held_fixture(**request_changes):
    sites, observations, request, owners, admission = fixture(**request_changes)
    held = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
    return sites, observations, request, owners, admission, held


def outcome(**changes):
    return dict(dict(source='observer', observed_at=1300, job_id='train-70b', metric=pb.METRIC, achieved=0.95), **changes)


def fixture(**request_changes):
    sites, observations, job = cs.scenario()
    request = cs.CrossSiteRequest('train-70b', job, 'site-a', 'site-b', 'ab-1', 'ab-2', protection_required=True,
                                  objective=0.9)
    request = dataclasses.replace(request, **request_changes)
    return sites, observations, request, cs.owners_for(sites, observations), cs.admission_check(sites)


class PlanStages(unittest.TestCase):
    def test_plan_binds_domain_results_paths_and_protection(self):
        sites, observations, request, owners, admission = fixture()
        plan = cs.plan(request, sites, observations, now=1100)
        self.assertEqual((plan.option, plan.stage, plan.status), ('stitch', 'admissible', 'ok'))
        roles = {c.role for c in plan.claims}
        self.assertTrue({'compute', 'power', 'working-path', 'protection-path'} <= roles)
        self.assertEqual(plan.domain['admission']['decision'], 'span')
        self.assertIn('stay', plan.domain['rejected'])            # missed deadline, from the ledger
        self.assertIn('move', plan.domain['rejected'])            # destination cannot hold the job
        self.assertTrue(any(g.startswith('span: XP1') for g in plan.not_checked))
        self.assertEqual(plan.valid_until, 1600)

    def test_full_sequence_reaches_verified_only_with_independent_outcome(self):
        sites, observations, request, owners, admission = fixture()
        effects = []
        held = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        done = pb.dispatch(held, owners, observations, 1120, effects.append, executions=pb.EffectLedger(),
                           admission=admission)
        self.assertEqual((done.stage, len(effects)), ('dispatched', 1))
        for bad in (dict(source='executor'), dict(observed_at=1120), dict(achieved=0.5), dict(job_id='other')):
            self.assertEqual(pb.verify(done, outcome(**bad)).status, 'unverified', bad)
        self.assertEqual(pb.verify(done, outcome()).stage, 'verified')


class Reservations(unittest.TestCase):
    def test_concurrent_requests_do_not_double_book(self):
        sites, observations, request, owners, admission = fixture(protection_required=False)
        # Path ab-1 carries 50 GB/s; each stitch claims 0.8 GB/s. Make accelerators plentiful so the path binds.
        for site in ('site-a', 'site-b'):
            owners[site].capacity['accelerators'] = 10**6
            owners[site].capacity['power'] = 10**9
        results, barrier = [], threading.Barrier(80)

        def attempt(i):
            plan = cs.plan(dataclasses.replace(request, job_id=f'job-{i}'), sites, observations, 1100)
            barrier.wait()
            results.append(pb.reserve(plan, owners, observations, 1110))
        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(80)]
        [t.start() for t in threads]; [t.join() for t in threads]
        reserved = [r for r in results if r.stage == 'reserved']
        self.assertEqual(len(reserved), 62)                       # floor(50e9 / 0.8e9)
        held = sum(g.amount for g in owners['transport'].grants.values() if g.resource == 'ab-1')
        self.assertLessEqual(held, 50_000_000_000)
        # Refused attempts that already held compute/power report them for compensation.
        partial = [r for r in results if r.status == 'partial']
        self.assertEqual(len(partial), 80 - 62)
        for r in partial:
            pb.compensate(r, owners, 1111)
        self.assertEqual(sum(g.amount for g in owners['site-a'].grants.values() if g.resource == 'accelerators'), 62 * 512)

    def test_expired_evidence_refuses_before_reservation_and_effect(self):
        sites, observations, request, owners, admission = fixture()
        plan = cs.plan(request, sites, observations, 1100)
        late = pb.reserve(plan, owners, observations, 1600, admission=admission)
        self.assertEqual(late.status, 'invalidated')
        self.assertFalse(owners['transport'].grants)
        stale = cs.plan(request, sites, observations, 1700)
        self.assertEqual(stale.status, 'refused')
        self.assertTrue(any('expired' in r for r in stale.reasons))

    def test_lease_expiry_fences_dispatch(self):
        sites, observations, request, owners, admission = fixture()
        held = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110,
                          lease_s=30, admission=admission)
        effects = []
        fenced = pb.dispatch(held, owners, observations, 1150, effects.append, executions=pb.EffectLedger(), admission=admission)
        self.assertEqual((fenced.status, effects), ('invalidated', []))
        self.assertTrue(any('lease' in r for r in fenced.reasons))

    def test_changed_path_invalidates_plan(self):
        sites, observations, request, owners, admission = fixture()
        held = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        changed = (dataclasses.replace(observations[0], topology_revision=cs.rev('ab-1/r2'), source_epoch=2),) + observations[1:]
        owners['transport'].publish_revision('ab-1', changed[0].topology_revision)
        effects = []
        result = pb.dispatch(held, owners, changed, 1120, effects.append, executions=pb.EffectLedger(), admission=admission)
        self.assertEqual((result.status, effects), ('invalidated', []))
        self.assertTrue(any('ab-1 changed' in r for r in result.reasons))
        self.assertTrue(any('revision changed after reservation' in r for r in result.reasons))

    def test_owner_refuses_reservation_against_old_revision(self):
        sites, observations, request, owners, admission = fixture()
        plan = cs.plan(request, sites, observations, 1100)
        owners['transport'].publish_revision('ab-1', cs.rev('ab-1/r2'))      # owner sees the change first
        result = pb.reserve(plan, owners, observations, 1110, admission=admission)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.grants)                                        # compute and power were taken
        self.assertIn('revision changed', result.reasons[0])
        undone = pb.compensate(result, owners, 1111)
        self.assertEqual(undone.status, 'compensated')
        self.assertFalse(any(owners[s].grants for s in ('site-a', 'site-b', 'transport')))
        pb.compensate(result, owners, 1111)                                         # idempotent

    def test_partial_success_is_inspectable_and_recoverable(self):
        sites, observations, request, owners, admission = fixture()
        owners['transport'].capacity['ab-2'] = 1                              # protection cannot be held
        result = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        self.assertEqual(result.status, 'partial')
        self.assertEqual([g.resource for g in result.grants], ['accelerators', 'power', 'accelerators', 'power', 'ab-1'])
        self.assertIn('ab-2', result.reasons[0])
        with self.assertRaises(ValueError):
            pb.dispatch(result, owners, observations, 1120, lambda p: None, executions=pb.EffectLedger())
        pb.compensate(result, owners, 1111)
        self.assertFalse(owners['transport'].grants)

    def test_lost_reply_is_unknown_then_reconciled_without_double_booking(self):
        sites, observations, request, owners, admission = fixture()
        owners['transport'].lose_response.add('ab-2')
        plan = cs.plan(request, sites, observations, 1100)
        first = pb.reserve(plan, owners, observations, 1110, admission=admission)
        self.assertEqual((first.status, len(first.unknown)), ('outcome-unknown', 1))
        reconciled = pb.reconcile(first, owners, 1112)
        self.assertEqual((reconciled.stage, reconciled.status), ('reserved', 'ok'))
        # A blind retry with the same keys returns the same grants rather than taking more capacity.
        retry = pb.reserve(plan, owners, observations, 1113, admission=admission)
        self.assertEqual([g.token for g in retry.grants], [g.token for g in reconciled.grants])
        self.assertEqual(sum(g.amount for g in owners['transport'].grants.values() if g.resource == 'ab-2'), 800_000_000)

    def test_lost_reply_for_unapplied_request_reconciles_to_partial(self):
        sites, observations, request, owners, admission = fixture()
        owners['transport'].lose_response.add('ab-2')
        first = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        owners['transport'].release(first.unknown[0])                         # the owner never kept it
        result = pb.reconcile(first, owners, 1112)
        self.assertEqual(result.status, 'partial')
        self.assertTrue(any('not held' in r for r in result.reasons))


class Diversity(unittest.TestCase):
    def setUp(self):
        _, self.observations, _ = cs.scenario()

    def test_names_from_different_authorities_are_not_assumed_disjoint(self):
        ab1, ab2, ac1 = self.observations
        self.assertEqual(pb.diversity(ab1, ab2), 'disjoint')
        self.assertEqual(pb.diversity(ab2, ac1), 'unknown')                   # unknown SRLGs
        relabeled = dataclasses.replace(ac1, srlgs=('duct-north',))
        self.assertEqual(pb.diversity(ab2, relabeled), 'unknown')             # carrier vs dcim naming
        shared = dataclasses.replace(ab2, srlgs=('duct-east',))
        self.assertEqual(pb.diversity(ab1, shared), 'shared')

    def test_protection_must_be_proven_disjoint(self):
        sites, observations, request, owners, admission = fixture(protection_path='ac-1')
        refused = cs.plan(dataclasses.replace(request, working_path='ab-2'), sites, observations, 1100)
        self.assertEqual(refused.status, 'refused')
        self.assertTrue(any('no path proven disjoint' in r for r in refused.reasons))

    def test_unknown_measurements_refuse_instead_of_defaulting(self):
        sites, observations, request, owners, admission = fixture()
        blind = (dataclasses.replace(observations[0], capacity_bytes_per_s=None),) + observations[1:]
        self.assertTrue(any('capacity is unknown' in r for r in cs.plan(request, sites, blind, 1100).reasons))
        dark = (dataclasses.replace(observations[0], insertion_loss_db=None),) + observations[1:]
        self.assertTrue(any('optical-margin' in r for r in cs.plan(request, sites, dark, 1100).reasons))


class Contract(unittest.TestCase):
    def test_observation_validation(self):
        _, observations, _ = cs.scenario()
        base = observations[0]
        for change in (dict(expires_at=base.observed_at), dict(capacity_bytes_per_s=1.5),
                       dict(evidence_class='measured'), dict(sites=('site-a', 'site-a')),
                       dict(topology_revision='short'), dict(bit_error_rate=float('nan'))):
            with self.assertRaises(ValueError, msg=change):
                dataclasses.replace(base, **change)

    def test_idempotency_key_cannot_be_reused_for_another_request(self):
        owner = pb.SimulatedLeaseOwner('t', {'p': 10}, {'p': 'r'})
        owner.reserve('p', 4, key='k', revision='r', lease_s=10, now=0)
        with self.assertRaises(pb.Refused):
            owner.reserve('p', 5, key='k', revision='r', lease_s=10, now=1)

    def test_demonstrator_is_deterministic(self):
        first, second = cs.experiment(), cs.experiment()
        self.assertEqual(pb.digest(first), pb.digest(second))
        steps = {s['step']: s for s in first['steps']}
        self.assertEqual(steps['rival-reserve']['status'], 'refused')
        self.assertEqual(steps['revalidate-after-degradation']['status'], 'invalidated')
        self.assertEqual(steps['replan-protect-via-unknown-diversity']['status'], 'refused')
        self.assertEqual(steps['reserve-without-protection']['stage'], 'reserved')
        self.assertEqual(steps['replay-reserved-snapshot']['status'], 'duplicate')
        self.assertEqual(first['effects_run'], 1)


class ReviewFindings(unittest.TestCase):
    """Regressions for Codex's review of PR #7 at d65d61f (findings 1-5)."""

    def test_1_replayed_snapshot_runs_the_effect_once(self):
        sites, observations, request, owners, admission, held = held_fixture()
        executions, effects = pb.EffectLedger(), []
        done = pb.dispatch(held, owners, observations, 1120, effects.append, executions=executions, admission=admission)
        replay = pb.dispatch(held, owners, observations, 1121, effects.append, executions=executions, admission=admission)
        self.assertEqual((done.stage, replay.stage, replay.status, len(effects)), ('dispatched', 'reserved', 'duplicate', 1))
        self.assertIn('already done', replay.reasons[0])

    def test_1_concurrent_dispatch_of_one_snapshot_runs_the_effect_once(self):
        sites, observations, request, owners, admission, held = held_fixture()
        executions, effects, results, barrier = pb.EffectLedger(), [], [], threading.Barrier(32)

        def attempt():
            barrier.wait()
            results.append(pb.dispatch(held, owners, observations, 1120, effects.append, executions=executions))
        threads = [threading.Thread(target=attempt) for _ in range(32)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(len(effects), 1)
        self.assertEqual(sorted(r.status for r in results).count('duplicate'), 31)
        self.assertEqual([r.stage for r in results].count('dispatched'), 1)

    def test_1_ambiguous_effect_is_not_retried_until_resolved(self):
        sites, observations, request, owners, admission, held = held_fixture()
        executions, calls = pb.EffectLedger(), []

        def flaky(plan):
            calls.append(plan.plan_id)
            raise TimeoutError('executor reply lost')
        unknown = pb.dispatch(held, owners, observations, 1120, flaky, executions=executions)
        self.assertEqual((unknown.stage, unknown.status), ('reserved', 'effect-unknown'))
        retry = pb.dispatch(held, owners, observations, 1121, calls.append, executions=executions)
        self.assertEqual((retry.status, len(calls)), ('duplicate', 1))
        self.assertIn('already unknown', retry.reasons[0])
        with self.assertRaises(ValueError):
            executions.resolve(pb.execution_id(held), False, 'executor')
        executions.resolve(pb.execution_id(held), False, 'synthetic-progress-observer')   # observed: it never ran
        self.assertEqual(pb.dispatch(held, owners, observations, 1122, calls.append, executions=executions).stage,
                         'dispatched')
        self.assertEqual(len(calls), 2)

    def test_2_compensation_resolves_unknown_keys_or_stays_pending(self):
        sites, observations, request, owners, admission = fixture()
        owners['transport'].lose_response.add('ab-2')
        unknown = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        self.assertEqual(unknown.status, 'outcome-unknown')
        lost_key = unknown.unknown[0]
        owners['transport'].lose_release.add(lost_key)                        # the release itself is lost
        pending = pb.compensate(unknown, owners, 1111)
        self.assertEqual(pending.status, 'compensation-pending')
        self.assertEqual([g.key for g in pending.grants], [lost_key])
        self.assertTrue(owners['transport'].grants)
        done = pb.compensate(pending, owners, 1112)
        self.assertEqual(done.status, 'compensated')
        self.assertFalse(any(owners[s].grants for s in ('site-a', 'site-b', 'transport')))
        # Without a lost release, the unknown key is released and confirmed in one pass.
        sites, observations, request, owners, admission = fixture()
        owners['transport'].lose_response.add('ab-2')
        unknown = pb.reserve(cs.plan(request, sites, observations, 1100), owners, observations, 1110, admission=admission)
        self.assertEqual(pb.compensate(unknown, owners, 1111).status, 'compensated')
        self.assertFalse(owners['transport'].grants)

    def test_3_verification_rejects_nonfinite_boolean_and_unbound_values(self):
        sites, observations, request, owners, admission, held = held_fixture()
        done = pb.dispatch(held, owners, observations, 1120, lambda p: None, executions=pb.EffectLedger())
        for bad in (dict(achieved=float('nan')), dict(achieved=float('inf')), dict(achieved=True),
                    dict(achieved=-0.5), dict(objective=-1, achieved=0), dict(objective=True, achieved=True),
                    dict(objective=float('nan')), dict(objective=0.5, achieved=0.6), dict(metric='device-healthy'),
                    dict(metric=None)):
            self.assertEqual(pb.verify(done, outcome(**bad)).status, 'unverified', bad)
        self.assertEqual(pb.verify(done, outcome(objective=0.9)).stage, 'verified')
        unbound = pb.advance(done, objective=None)
        self.assertIn('plan has no bound objective', pb.verify(unbound, outcome()).reasons)
        for bad in (0, -1, 1.5, True, float('nan')):
            with self.assertRaises(ValueError, msg=bad):
                pb.advance(done, objective=bad)

    def test_4_protection_needs_matching_endpoints_and_admission(self):
        for changes, reason in ((dict(sites=('other-a', 'other-b')), 'not the working endpoints'),
                                (dict(rtt_us=None, insertion_loss_db=None, bit_error_rate=None), 'protection admission'),
                                (dict(bit_error_rate=None), 'protection admission')):
            sites, observations, request, owners, admission = fixture()
            bad = (observations[0], dataclasses.replace(observations[1], **changes)) + observations[2:]
            plan = cs.plan(request, sites, bad, 1100)
            self.assertEqual(plan.status, 'refused', changes)
            self.assertTrue(any(reason in r for r in plan.reasons), (changes, plan.reasons))
        sites, observations, request, owners, admission = fixture(protection_required=False)
        wrong = (dataclasses.replace(observations[0], sites=('site-a', 'site-c')),) + observations[1:]
        self.assertTrue(any('not site-a/site-b' in r for r in cs.plan(request, sites, wrong, 1100).reasons))

    def test_4_protection_admission_is_rechecked_before_dispatch(self):
        sites, observations, request, owners, admission, held = held_fixture()
        self.assertEqual(held.domain['protection_admission']['decision'], 'span')
        moved = (observations[0], dataclasses.replace(observations[1], topology_revision=cs.rev('ab-2/r2'))) + observations[2:]
        # The protection path's plant revision moved while its observation epoch did not.
        reasons = admission(held, {o.path_id: o for o in moved}, 1120)['reasons']
        self.assertTrue(reasons and all(r.startswith('protection ab-2: ') for r in reasons), reasons)

    def test_5_replaced_source_or_evidence_class_invalidates(self):
        for changes in (dict(source='untrusted-other-source'), dict(evidence_class='emulated')):
            sites, observations, request, owners, admission, held = held_fixture()
            bad = (dataclasses.replace(observations[0], **changes),) + observations[1:]
            effects = []
            result = pb.dispatch(held, owners, bad, 1120, effects.append, executions=pb.EffectLedger(), admission=admission)
            self.assertEqual((result.status, effects), ('invalidated', []), changes)
            self.assertTrue(any('not the planned synthetic-path-collector (synthetic)' in r for r in result.reasons))



class ReReviewFindings(unittest.TestCase):
    """Regressions for Codex's re-review of PR #7 at fc865fc (findings 1-3)."""

    def test_1_reconciled_grant_order_does_not_change_the_execution(self):
        sites, observations, request, owners, admission = fixture()
        owners['site-a'].lose_response.add('power')
        plan = cs.plan(request, sites, observations, 1100)
        unknown = pb.reserve(plan, owners, observations, 1110, admission=admission)
        reconciled = pb.reconcile(unknown, owners, 1111)                     # lost grant appended last
        retried = pb.reserve(plan, owners, observations, 1112, admission=admission)   # grants in claim order
        self.assertEqual(sorted(reconciled.grants, key=lambda g: g.key), sorted(retried.grants, key=lambda g: g.key))
        self.assertNotEqual(reconciled.grants, retried.grants)
        executions, effects = pb.EffectLedger(), []
        first = pb.dispatch(reconciled, owners, observations, 1120, effects.append, executions=executions)
        second = pb.dispatch(retried, owners, observations, 1121, effects.append, executions=executions)
        reversed_ = pb.dispatch(pb.advance(retried, grants=retried.grants[::-1]), owners, observations, 1122,
                                effects.append, executions=executions)
        self.assertEqual((first.stage, second.status, reversed_.status, len(effects)),
                         ('dispatched', 'duplicate', 'duplicate', 1))

    def test_1_ambiguous_effect_after_recovery_blocks_the_retried_reservation(self):
        sites, observations, request, owners, admission = fixture()
        owners['site-a'].lose_response.add('power')                          # claim 1 of 6: not the last
        plan = cs.plan(request, sites, observations, 1100)
        reconciled = pb.reconcile(pb.reserve(plan, owners, observations, 1110, admission=admission), owners, 1111)
        retried = pb.reserve(plan, owners, observations, 1112, admission=admission)
        executions, effects = pb.EffectLedger(), []

        def ambiguous(plan):
            effects.append(plan)
            raise RuntimeError('lost reply')
        first = pb.dispatch(reconciled, owners, observations, 1120, ambiguous, executions=executions)
        second = pb.dispatch(retried, owners, observations, 1121, effects.append, executions=executions)
        self.assertEqual((first.status, second.status, len(effects)), ('effect-unknown', 'duplicate', 1))

    def test_2_endpoint_or_shared_risk_change_at_same_revision_invalidates(self):
        for index, changes in ((1, dict(sites=('site-a', 'site-c'))), (1, dict(srlgs=('duct-east',))),
                               (1, dict(members=(pb.Member('dcim', 'duct', 'east'),))),
                               (1, dict(srlgs=('duct-east',), members=(pb.Member('dcim', 'duct', 'east'),
                                                                        pb.Member('ols', 'och', 'ab-1')))),
                               (0, dict(sites=('site-b', 'site-c')))):
            sites, observations, request, owners, admission, held = held_fixture()
            moved = list(observations)
            moved[index] = dataclasses.replace(observations[index], **changes)          # same revision and epoch
            effects = []
            result = pb.dispatch(held, owners, tuple(moved), 1120, effects.append, executions=pb.EffectLedger(),
                                 admission=admission)
            self.assertEqual((result.status, effects), ('invalidated', []), changes)
            self.assertTrue(any('shared-risk groups or members changed' in r for r in result.reasons), result.reasons)
        # Reordering the same endpoints or groups is not a change.
        sites, observations, request, owners, admission, held = held_fixture()
        same = (dataclasses.replace(observations[0], sites=('site-b', 'site-a')),) + observations[1:]
        self.assertEqual(pb.revalidate(held, same, 1120), ())

    def test_3_huge_integers_are_out_of_domain_not_overflow(self):
        sites, observations, request, owners, admission, held = held_fixture()
        done = pb.dispatch(held, owners, observations, 1120, lambda p: None, executions=pb.EffectLedger())
        for huge in (10**400, -10**400):
            for bad in (dict(achieved=huge), dict(objective=huge)):
                result = pb.verify(done, outcome(**bad))
                self.assertEqual(result.status, 'unverified', bad)
            with self.assertRaises(ValueError):
                pb.advance(done, objective=huge)
            for name in ('insertion_loss_db', 'bit_error_rate'):
                with self.assertRaises(ValueError, msg=(name, huge > 0)):
                    dataclasses.replace(observations[0], **{name: huge})


class FreshnessFindings(unittest.TestCase):
    """Regressions for Codex's review of PR #7 at 9f8b312: path evidence dated after the check time."""

    def test_future_dated_evidence_is_a_structured_planning_refusal(self):
        for index, reason in ((0, 'path ab-1 observation is dated 1900, after the planning time 1100'),
                              (1, 'protection: path ab-2 observation is dated 1900, after the planning time 1100')):
            sites, observations, request, owners, admission = fixture()
            moved = list(observations)
            moved[index] = dataclasses.replace(observations[index], observed_at=1900, expires_at=2000)
            plan = cs.plan(request, sites, tuple(moved), 1100)             # raised ValueError at 9f8b312
            self.assertEqual(plan.status, 'refused', index)
            self.assertTrue(any(reason in r for r in plan.reasons), plan.reasons)

    def test_future_dated_evidence_refuses_reservation_before_any_grant(self):
        sites, observations, request, owners, admission = fixture()
        plan = cs.plan(request, sites, observations, 1100)
        future = (dataclasses.replace(observations[0], observed_at=1900, expires_at=2000),) + observations[1:]
        result = pb.reserve(plan, owners, future, 1110, admission=admission)
        self.assertEqual((result.status, result.grants), ('invalidated', ()))
        self.assertTrue(any('ab-1 observation is dated 1900, after the check time 1110' in r for r in result.reasons))
        self.assertFalse(any(owners[s].grants for s in ('site-a', 'site-b', 'transport')))

    def test_future_dated_evidence_refuses_dispatch_before_any_effect(self):
        for index, observed, expires in ((0, 1900, 2000), (0, 1121, 1600), (1, 1121, 1600)):
            sites, observations, request, owners, admission, held = held_fixture()
            moved = list(observations)
            moved[index] = dataclasses.replace(observations[index], observed_at=observed, expires_at=expires)
            effects = []
            result = pb.dispatch(held, owners, tuple(moved), 1120, effects.append, executions=pb.EffectLedger(),
                                 admission=admission)
            self.assertEqual((result.status, effects), ('invalidated', []), (index, observed))
            path = observations[index].path_id
            self.assertTrue(any(f'{path} observation is dated {observed}, after the check time 1120' in r
                                for r in result.reasons), result.reasons)

    def test_evidence_observed_at_the_check_time_is_accepted_and_expiry_still_fences(self):
        sites, observations, request, owners, admission = fixture()
        at = lambda t, **changes: tuple(dataclasses.replace(o, observed_at=t, **changes) for o in observations)
        plan = cs.plan(request, sites, at(1100), 1100)
        self.assertEqual((plan.stage, plan.status), ('admissible', 'ok'))
        held = pb.reserve(plan, owners, at(1110), 1110, admission=admission)
        self.assertEqual((held.stage, held.status), ('reserved', 'ok'))
        expiring = pb.dispatch(held, owners, at(1100, expires_at=1120), 1120, lambda p: None,
                               executions=pb.EffectLedger(), admission=admission)
        self.assertEqual(expiring.status, 'invalidated')
        self.assertTrue(any('observation expired' in r for r in expiring.reasons))
        effects = []
        done = pb.dispatch(held, owners, at(1120), 1120, effects.append, executions=pb.EffectLedger(),
                           admission=admission)
        self.assertEqual((done.stage, len(effects)), ('dispatched', 1))

    def test_span_admission_refuses_rather_than_raises_on_future_evidence(self):
        sites, observations, request, owners, admission, held = held_fixture()
        rect = dict(held.domain['geometry:site-a']['requests'][0], site='site-a')
        future = dataclasses.replace(observations[0], observed_at=1900, expires_at=2000)
        verdict = cs.span_admission('train-70b', rect, future, 100, 1100, future.topology_revision)
        self.assertEqual((verdict['decision'], verdict['passes_supplied_checks']), ('deny', False))
        self.assertTrue(cs.admission_refusals(verdict))

    def test_admission_measures_age_from_the_older_observation_time(self):
        sites, observations, request, owners, admission, held = held_fixture()
        latest = lambda t: {o.path_id: dataclasses.replace(o, observed_at=t) for o in observations}
        self.assertTrue(admission(held, latest(1110), 1120)['passes_supplied_checks'])   # planned 1000: 120 s old
        for current, now in ((700, 1120), (1340, 1350)):      # back-dated: 420 s; refreshed: planned is 350 s old
            verdict = admission(held, latest(current), now)
            self.assertFalse(verdict['passes_supplied_checks'], current)
            self.assertTrue(any('past the 300s TTL' in r for r in verdict['reasons']), verdict['reasons'])
        # Older evidence still inside the TTL passes. Whether evidence may go backwards at all is an open
        # contract question (Codex, 9f8b312 review), not decided here.
        self.assertTrue(admission(held, latest(900), 1120)['passes_supplied_checks'])

if __name__ == '__main__':
    unittest.main()
