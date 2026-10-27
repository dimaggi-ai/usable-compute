"""Bind a cross-site placement to the paths and reservations it depends on.

`dimaggi-path-binding/v1` is a narrow exchange contract proposed for Stage 1 of
the October 2026 networking build. It does not replace any domain model.
slicepacker places, span-contract admits, compute-power-placement prices
stay/stitch/move; this module joins their results to path evidence and to
reservations held by a named owner, then tracks five separate stages:

    modeled-feasible -> admissible -> reserved -> dispatched -> verified

Everything here is simulated. `SimulatedLeaseOwner` is an in-process stand-in
for an authoritative reservation owner. Its lock protects nothing outside this
process, so it does not qualify any real controller path.
"""
import dataclasses
import hashlib
import json
import math
import threading

SCHEMA = 'dimaggi-path-binding/v1'
EVIDENCE_CLASSES = ('synthetic', 'emulated', 'lab-measured', 'deployment-measured')
STAGES = ('modeled-feasible', 'admissible', 'reserved', 'dispatched', 'verified')
ROLES = ('compute', 'power', 'working-path', 'protection-path', 'transfer-path')
MAX_INT = 2**53 - 1
# The one outcome measure a plan binds: achieved step throughput over the plan's
# modeled step throughput, reported by an observer independent of the executor.
METRIC = 'goodput-fraction'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _count(name, value, *, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value <= MAX_INT:
        raise ValueError(f'{name} must be an exact nonnegative integer')
    return value


def _real(value):
    """A finite int or float; booleans are not measurements."""
    return type(value) in (int, float) and math.isfinite(value)


@dataclasses.dataclass(frozen=True)
class Member:
    """One physical element, named by the authority that owns its inventory.

    Names from different authorities never prove that two elements differ.
    """
    authority: str
    kind: str
    ident: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v for v in (self.authority, self.kind, self.ident)):
            raise ValueError('member authority, kind and ident are required')


@dataclasses.dataclass(frozen=True)
class PathObservation:
    """A source-attributed, expiring observation of one inter-site path.

    Unknown capacity, delay, optical margin or shared-risk groups stay None;
    callers must not substitute a default.
    """
    path_id: str
    sites: tuple
    topology_revision: str
    source: str
    source_epoch: int
    observed_at: int
    expires_at: int
    evidence_class: str
    members: tuple = ()
    srlgs: tuple = None
    capacity_bytes_per_s: int = None
    rtt_us: int = None
    insertion_loss_db: float = None
    bit_error_rate: float = None

    def __post_init__(self):
        if not self.path_id or not self.source or len(self.topology_revision) != 64:
            raise ValueError('path_id, source and a 64-hex topology_revision are required')
        if len(self.sites) != 2 or self.sites[0] == self.sites[1]:
            raise ValueError('a path joins two distinct sites')
        for name in ('source_epoch', 'observed_at', 'expires_at'):
            _count(name, getattr(self, name))
        _count('capacity_bytes_per_s', self.capacity_bytes_per_s, optional=True)
        _count('rtt_us', self.rtt_us, optional=True)
        if self.expires_at <= self.observed_at:
            raise ValueError('evidence must expire after it was observed')
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f'evidence_class must be one of {EVIDENCE_CLASSES}')
        for name in ('insertion_loss_db', 'bit_error_rate'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f'{name} must be finite and nonnegative or unknown')
        if not all(isinstance(m, Member) for m in self.members):
            raise ValueError('members must be Member records')

    def ref(self):
        return dict(path_id=self.path_id, topology_revision=self.topology_revision,
                    source=self.source, source_epoch=self.source_epoch,
                    observed_at=self.observed_at, expires_at=self.expires_at,
                    evidence_class=self.evidence_class)


def diversity(a, b):
    """Return 'disjoint', 'shared' or 'unknown' for two path observations.

    Disjoint needs known shared-risk groups on both paths, no common group,
    no common member, and members of each kind named by the same authorities.
    """
    if a.srlgs is None or b.srlgs is None or not a.members or not b.members:
        return 'unknown'
    if set(a.srlgs) & set(b.srlgs) or set(a.members) & set(b.members):
        return 'shared'
    kinds = {m.kind for m in a.members} | {m.kind for m in b.members}
    for kind in kinds:
        if {m.authority for m in a.members if m.kind == kind} != {m.authority for m in b.members if m.kind == kind}:
            return 'unknown'
    return 'disjoint'


@dataclasses.dataclass(frozen=True)
class Claim:
    owner: str
    resource: str
    amount: int
    unit: str
    role: str
    revision: str = None

    def __post_init__(self):
        _count('amount', self.amount)
        if self.role not in ROLES:
            raise ValueError(f'role must be one of {ROLES}')


@dataclasses.dataclass(frozen=True)
class Grant:
    owner: str
    resource: str
    amount: int
    key: str
    token: int
    revision: str
    expires_at: int


class Refused(Exception):
    pass


class LostResponse(Exception):
    """The owner may have applied the request, but no reply arrived."""


class SimulatedLeaseOwner:
    """In-process reservation owner with leases, fencing and idempotency keys.

    `lose_response` holds resources whose next reply is dropped after the
    reservation is applied, to exercise unknown-outcome reconciliation.
    `lose_release` holds keys whose next release request is lost before the
    owner applies it, so the caller cannot tell whether the grant was freed.
    """

    def __init__(self, name, capacities, revisions=None):
        self.name = name
        self.capacity = {r: _count(r, c) for r, c in capacities.items()}
        self.revision = dict(revisions or {})
        self.grants = {}
        self.token = 0
        self.lose_response = set()
        self.lose_release = set()
        self.lock = threading.Lock()

    def _held(self, resource, now):
        return sum(g.amount for g in self.grants.values() if g.resource == resource and g.expires_at > now)

    def publish_revision(self, resource, revision):
        with self.lock:
            self.revision[resource] = revision

    def reserve(self, resource, amount, *, key, revision, lease_s, now):
        with self.lock:
            existing = self.grants.get(key)
            if existing is not None and existing.expires_at > now:
                if (existing.resource, existing.amount) != (resource, amount):
                    raise Refused(f'idempotency key {key} was used for a different request')
                return existing
            if resource not in self.capacity:
                raise Refused(f'{self.name} does not own {resource}')
            if self.revision.get(resource) != revision:
                raise Refused(f'{resource} revision changed since the plan was made')
            if self._held(resource, now) + amount > self.capacity[resource]:
                raise Refused(f'{resource} has insufficient unreserved capacity')
            self.token += 1
            grant = Grant(self.name, resource, amount, key, self.token, revision, now + lease_s)
            self.grants[key] = grant
            if resource in self.lose_response:
                self.lose_response.discard(resource)
                raise LostResponse(key)
            return grant

    def lookup(self, key, now):
        with self.lock:
            grant = self.grants.get(key)
            return grant if grant is not None and grant.expires_at > now else None

    def release(self, key):
        with self.lock:
            if key in self.lose_release:
                self.lose_release.discard(key)
                raise LostResponse(key)
            return self.grants.pop(key, None) is not None

    def check(self, grant, now):
        """Fencing check immediately before an effect."""
        with self.lock:
            current = self.grants.get(grant.key)
            if current is None or current.token != grant.token:
                raise Refused(f'grant {grant.key} was superseded or released')
            if current.expires_at <= now:
                raise Refused(f'lease on {grant.resource} expired')
            if self.revision.get(grant.resource) != grant.revision:
                raise Refused(f'{grant.resource} revision changed after reservation')


@dataclasses.dataclass(frozen=True)
class Plan:
    """One binding. `stage` is the furthest stage reached; `status` says how it ended.

    `objective` is the minimum METRIC value, in (0, 1], that verification
    requires. It is fixed when the plan is made, not taken from the outcome.
    """
    plan_id: str
    job_id: str
    option: str
    stage: str
    status: str
    claims: tuple
    evidence: tuple
    valid_until: int
    domain: dict
    not_checked: tuple
    reasons: tuple = ()
    grants: tuple = ()
    unknown: tuple = ()
    dispatched_at: int = None
    objective: float = None

    def __post_init__(self):
        if self.objective is not None and not (_real(self.objective) and 0 < self.objective <= 1):
            raise ValueError(f'objective must be a {METRIC} in (0, 1]')

    def to_dict(self):
        out = dataclasses.asdict(self)
        out['schema'] = SCHEMA
        return out


def advance(plan, **changes):
    return dataclasses.replace(plan, **changes)


def claim_key(plan, index):
    claim = plan.claims[index]
    return f'{plan.plan_id[:16]}:{index}:{claim.owner}:{claim.resource}'


def revalidate(plan, observations, now, admission=None):
    """Re-check evidence freshness, path revisions and admission before any effect."""
    latest = {o.path_id: o for o in observations}
    reasons = []
    if now >= plan.valid_until:
        reasons.append(f'evidence expired at {plan.valid_until}')
    for ref in plan.evidence:
        current = latest.get(ref['path_id'])
        if current is None:
            reasons.append(f"path {ref['path_id']} has no current observation")
        elif (current.source, current.evidence_class) != (ref['source'], ref['evidence_class']):
            reasons.append(f"path {ref['path_id']} is now reported by {current.source} ({current.evidence_class}), "
                           f"not the planned {ref['source']} ({ref['evidence_class']})")
        elif current.topology_revision != ref['topology_revision'] or current.source_epoch != ref['source_epoch']:
            reasons.append(f"path {ref['path_id']} changed since the plan was made")
        elif now >= current.expires_at:
            reasons.append(f"path {ref['path_id']} observation expired")
    if admission is not None and not reasons:
        verdict = admission(plan, latest, now)
        if verdict is not None and not verdict['passes_supplied_checks']:
            reasons.extend(['admission: ' + r for r in verdict['reasons']] or ['admission refused'])
    return tuple(reasons)


def reserve(plan, owners, observations, now, *, lease_s=300, admission=None):
    """Reserve every claim in order, all-or-nothing from the caller's view.

    A refusal after earlier grants leaves `status='partial'` with those grants
    listed, so `compensate` or `reconcile` can act on them. A lost reply leaves
    `status='outcome-unknown'`; nothing assumes the request failed.
    """
    if plan.status != 'ok' or plan.stage != 'admissible':
        raise ValueError('only an admissible plan can be reserved')
    reasons = revalidate(plan, observations, now, admission)
    if reasons:
        return advance(plan, status='invalidated', reasons=reasons)
    grants, unknown = [], []
    lease_s = min(lease_s, plan.valid_until - now)
    for index, claim in enumerate(plan.claims):
        key = claim_key(plan, index)
        try:
            grants.append(owners[claim.owner].reserve(claim.resource, claim.amount, key=key,
                          revision=claim.revision, lease_s=lease_s, now=now))
        except LostResponse:
            unknown.append(key)
        except Refused as exc:
            status = 'partial' if grants or unknown else 'refused'
            return advance(plan, status=status, reasons=(str(exc),), grants=tuple(grants), unknown=tuple(unknown))
    if unknown:
        return advance(plan, status='outcome-unknown', grants=tuple(grants), unknown=tuple(unknown))
    return advance(plan, stage='reserved', grants=tuple(grants))


def reconcile(plan, owners, now):
    """Ask each owner what it actually holds for the plan's unknown keys."""
    by_key = {claim_key(plan, i): c for i, c in enumerate(plan.claims)}
    grants, missing = list(plan.grants), []
    for key in plan.unknown:
        found = owners[by_key[key].owner].lookup(key, now)
        (grants.append(found) if found else missing.append(key))
    if len(grants) == len(plan.claims) and plan.status == 'outcome-unknown':
        return advance(plan, stage='reserved', status='ok', grants=tuple(grants), unknown=())
    return advance(plan, status='partial', grants=tuple(grants), unknown=(),
                   reasons=plan.reasons + tuple(f'{k} not held by its owner' for k in missing))


def compensate(plan, owners, now):
    """Release every grant and unknown-outcome key the plan may hold, then confirm with each owner.

    The plan is 'compensated' only when no owner still holds any of its keys.
    A key that is still held (for example because the release was lost) leaves
    'compensation-pending' with those grants listed. Safe to repeat.
    """
    owner_of = {claim_key(plan, i): c.owner for i, c in enumerate(plan.claims)}
    keys = [(g.owner, g.key) for g in plan.grants] + [(owner_of[k], k) for k in plan.unknown]
    held = []
    for owner, key in keys:
        try:
            owners[owner].release(key)
        except LostResponse:
            pass
        found = owners[owner].lookup(key, now)
        if found is not None:
            held.append(found)
    if held:
        return advance(plan, status='compensation-pending', grants=tuple(held), unknown=(),
                       reasons=tuple(f'{g.key} still held by {g.owner}' for g in held))
    return advance(plan, status='compensated', grants=(), unknown=())


class EffectLedger:
    """Authoritative record of the effect run for each reservation (in-process simulation).

    The execution identity is the plan id plus every grant's owner, key and
    fencing token, so one reservation can start its effect once, including
    under concurrent dispatch of the same snapshot. States are 'running',
    'done' and 'unknown'. An effect that ends without a clear result stays
    'unknown' and is not retried until an independent observer resolves it.
    """

    def __init__(self):
        self.records = {}
        self.lock = threading.Lock()

    def begin(self, execution, now):
        with self.lock:
            record = self.records.get(execution)
            if record is not None:
                raise Refused(f"effect for this reservation is already {record['state']}")
            self.records[execution] = {'state': 'running', 'started_at': now}

    def finish(self, execution, state):
        with self.lock:
            self.records[execution]['state'] = state

    def state(self, execution):
        with self.lock:
            record = self.records.get(execution)
            return record and record['state']

    def resolve(self, execution, ran, source):
        """Settle an unknown execution from an independent observation of whether the effect ran."""
        if source in (None, 'planner', 'executor'):
            raise ValueError('only an independent observer can resolve an unknown effect')
        with self.lock:
            record = self.records.get(execution)
            if record is None or record['state'] != 'unknown':
                raise Refused('only an unknown effect can be resolved')
            if ran:
                record['state'] = 'done'
            else:
                del self.records[execution]


def execution_id(plan):
    return digest([plan.plan_id, [[g.owner, g.key, g.token] for g in plan.grants]])


def dispatch(plan, owners, observations, now, effect, *, executions, admission=None):
    """Revalidate and fence every grant, then run the bounded effect once per reservation.

    `executions` is the EffectLedger that records the effect. Dispatching the
    same reserved snapshot again, or concurrently, returns 'duplicate' without
    running it. An effect that raises leaves 'effect-unknown', not a retry.
    """
    if plan.stage != 'reserved' or plan.status != 'ok':
        raise ValueError('only a fully reserved plan can be dispatched')
    reasons = list(revalidate(plan, observations, now, admission))
    for grant in plan.grants:
        try:
            owners[grant.owner].check(grant, now)
        except Refused as exc:
            reasons.append(str(exc))
    if reasons:
        return advance(plan, status='invalidated', reasons=tuple(reasons))
    execution = execution_id(plan)
    try:
        executions.begin(execution, now)
    except Refused as exc:
        return advance(plan, status='duplicate', reasons=(str(exc),))
    try:
        effect(plan)
    except Exception as exc:
        executions.finish(execution, 'unknown')
        return advance(plan, status='effect-unknown',
                       reasons=(f'effect ended without a clear result ({type(exc).__name__}); resolve before any retry',))
    executions.finish(execution, 'done')
    return advance(plan, stage='dispatched', dispatched_at=now)


def verify(plan, outcome):
    """Accept only an independent outcome observed after dispatch that meets the plan's objective.

    The target comes from the plan. The outcome must measure METRIC with a
    finite, nonnegative number; an objective it reports must equal the plan's.
    A successful dispatch or healthy device signal is not a verified outcome.
    """
    if plan.stage != 'dispatched':
        raise ValueError('only a dispatched plan can be verified')
    problems = []
    if outcome.get('source') in (None, 'planner', 'executor'):
        problems.append('outcome is not from an independent observer')
    if type(outcome.get('observed_at')) is not int or outcome['observed_at'] <= plan.dispatched_at:
        problems.append('outcome was not observed after dispatch')
    if outcome.get('job_id') != plan.job_id:
        problems.append('outcome names a different job')
    target, achieved = plan.objective, outcome.get('achieved')
    if target is None:
        problems.append('plan has no bound objective')
    if outcome.get('metric') != METRIC:
        problems.append(f'outcome does not measure {METRIC}')
    if 'objective' in outcome and (not _real(outcome['objective']) or outcome['objective'] != target):
        problems.append('outcome objective differs from the plan')
    if not _real(achieved) or achieved < 0:
        problems.append('achieved value is not a finite nonnegative measurement')
    elif target is not None and achieved < target:
        problems.append('objective not met')
    if problems:
        return advance(plan, status='unverified', reasons=tuple(problems))
    return advance(plan, stage='verified')
