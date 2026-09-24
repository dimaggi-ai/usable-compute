"""Bounded read-only topology snapshots. Replay qualification, not hardware truth.

Producer sequence is local adapter order, never a parsed Kubernetes resourceVersion.
Sources are independent; conflicts and partial coverage cannot disappear in a merge.
"""
from copy import deepcopy
from .observations import ObservationError, _digest, _identifier, _utc, _json

MAX_RECORDS = 10000
MAX_BYTES = 8 * 1024 * 1024


def need(condition, reason):
    if not condition:
        raise ObservationError(reason)


def bounded(value):
    need(len(_json(value).encode()) <= MAX_BYTES, 'topology byte limit exceeded')


def topograph(payload, *, version):
    need(version == '1.0.0', 'unqualified Topograph version')
    bounded(payload)
    need(type(payload) is dict and set(payload) == {'instances'}, 'unsupported graph output')
    rows = payload['instances']
    need(type(rows) is list and len(rows) <= MAX_RECORDS, 'invalid graph instance count')
    result = {}
    for row in rows:
        need(type(row) is dict and set(row) == {'id', 'network_layers', 'labels'}, 'unsupported instance fields')
        name = _identifier(row['id'], 'instance ID')
        need(name not in result, 'duplicate instance identity')
        need(type(row['network_layers']) is list and len(row['network_layers']) <= 32, 'invalid network layers')
        for layer in row['network_layers']:
            _identifier(layer, 'network layer')
        need(type(row['labels']) is dict and len(row['labels']) <= 256, 'invalid labels')
        for key, value in row['labels'].items():
            _identifier(key, 'label key'); _identifier(value, 'label value')
        result[name] = {'uid': name, 'kind': 'provider-instance', 'attributes': deepcopy(row),
                        'health': 'unknown', 'capacity': 'unknown'}
    return result, ['fabric_health_unchecked', 'device_capacity_unchecked', 'instance_incarnation_unproven']


def kubernetes(payload, *, version):
    need(version == '1.34.0', 'unqualified Kubernetes version')
    bounded(payload)
    need(type(payload) is dict and payload.get('apiVersion') == 'resource.k8s.io/v1'
         and payload.get('kind') == 'ResourceSliceList', 'unsupported DRA list contract')
    meta = payload.get('metadata', {})
    _identifier(meta.get('resourceVersion'), 'list resourceVersion')
    need(not meta.get('continue'), 'partial paginated list requires complete collection')
    rows = payload.get('items')
    need(type(rows) is list and len(rows) <= MAX_RECORDS, 'invalid DRA slice count')
    result, pools, devices = {}, {}, set()
    issues = {'device_health_unchecked', 'claims_and_permissions_unchecked', 'node_identity_unproven'}
    for row in rows:
        need(type(row) is dict, 'invalid resource slice')
        need(row.get('apiVersion') == 'resource.k8s.io/v1' and row.get('kind') == 'ResourceSlice', 'mixed DRA versions')
        metadata, spec = row['metadata'], row['spec']
        name = _identifier(metadata['name'], 'slice name')
        uid = _identifier(metadata['uid'], 'slice UID')
        _identifier(metadata['resourceVersion'], 'slice resourceVersion')
        need(name not in result, 'duplicate slice name')
        driver = _identifier(spec['driver'], 'driver')
        pool = spec['pool']; pool_name = _identifier(pool['name'], 'pool')
        generation, count = pool['generation'], pool['resourceSliceCount']
        need(type(generation) is int and generation >= 0 and type(count) is int and count > 0, 'invalid pool generation/count')
        pools.setdefault((driver, pool_name), []).append((generation, count))
        selections = [key for key in ('nodeName','nodeSelector','allNodes','perDeviceNodeSelection') if key in spec]
        need(len(selections) == 1, 'ambiguous node selection')
        if selections != ['nodeName']:
            issues.add('node_selection_unsupported')
        else:
            _identifier(spec['nodeName'], 'node name')
        device_rows = spec.get('devices', [])
        need(type(device_rows) is list and len(device_rows) <= 128, 'invalid device count')
        for device in device_rows:
            key = (driver, pool_name, generation, _identifier(device['name'], 'device name'))
            need(key not in devices, 'duplicate physical/partition device identity')
            devices.add(key)
            # Preserve the complete typed device description, including capacities,
            # attributes, selectors, taints and sharing fields; never sum partitions.
        result[name] = {'uid': uid, 'kind': 'dra-resource-slice', 'attributes': deepcopy(row),
                        'health': 'unknown', 'capacity': 'unknown'}
    for entries in pools.values():
        if len(set(entries)) != 1 or len(entries) != entries[0][1]:
            issues.add('pool_incomplete_or_generation_conflict')
    return result, sorted(issues)


class TopologyState:
    """Bounded in-memory read projection. Restarts require full snapshots.

    One instance is scoped to one tenant/cluster. Each update carries the digest
    of its predecessor. A gap/conflict taints the source until a new-epoch full
    relist; rejected data never advances the state. No source receives priority.
    """
    def __init__(self, tenant, cluster):
        self.tenant = _identifier(tenant, 'tenant')
        self.cluster = _identifier(cluster, 'cluster')
        self.sources = {}
        self.tainted = set()
        self.retired_epochs = {}

    def apply(self, event):
        bounded(event)
        keys = {'schema','tenant','cluster','source','epoch','sequence','previous',
                'observed_at','expires_at','producer_version','evidence_class','adapter', 'mode','payload'}
        need(type(event) is dict and set(event) == keys and event['schema'] == 'dimaggi-topology-input/v1', 'invalid topology event contract')
        need(event['tenant'] == self.tenant and event['cluster'] == self.cluster, 'cross-tenant/cluster topology refused')
        need(event['evidence_class'] in ('reference','replay','observed'), 'unsupported evidence class')
        source = _identifier(event['source'], 'source'); epoch = _identifier(event['epoch'], 'epoch')
        need(type(event['sequence']) is int and event['sequence'] >= 0, 'invalid source sequence')
        need(_utc(event['observed_at']) < _utc(event['expires_at']), 'invalid validity interval')
        need(event['mode'] == 'full', 'incremental updates require relist in this profile')
        adapter = {'topograph': topograph, 'kubernetes-dra': kubernetes}.get(event['adapter'])
        need(adapter is not None, 'unknown adapter')
        records, issues = adapter(event['payload'], version=event['producer_version'])
        prior = self.sources.get(source)
        fingerprint = _digest(event)
        if prior and fingerprint == prior['digest']:
            return 'duplicate'
        need(len(self.sources) < 32 or source in self.sources, 'source limit exceeded')
        need(epoch not in self.retired_epochs.get(source, set()), 'retired source epoch refused')
        if prior and epoch == prior['event']['epoch']:
            if source in self.tainted or event['sequence'] != prior['event']['sequence'] + 1 or event['previous'] != prior['digest']:
                self.tainted.add(source)
                raise ObservationError('source gap/conflict: new-epoch full relist required')
            if _utc(event['observed_at']) < _utc(prior['event']['observed_at']):
                self.tainted.add(source)
                raise ObservationError('observation time regressed')
        else:
            need(event['sequence'] == 0 and event['previous'] is None, 'new epoch must start with sequence zero and no predecessor')
            if prior:
                retired = self.retired_epochs.setdefault(source, set())
                need(len(retired) < 1024, 'epoch history limit: restart and rebootstrap required')
                need(_utc(event['observed_at']) >= _utc(prior['event']['observed_at']), 'relist time regressed')
                retired.add(prior['event']['epoch'])
        self.sources[source] = {'digest': fingerprint, 'event': deepcopy(event), 'records': records, 'issues': issues}
        self.tainted.discard(source)
        return 'changed'

    def snapshot(self, now):
        now = _utc(now)
        rows, issues = {}, []
        for source, state in sorted(self.sources.items()):
            event = state['event']
            problems = list(state['issues'])
            if not _utc(event['observed_at']) <= now < _utc(event['expires_at']):
                problems.append('stale_or_future')
            if source in self.tainted:
                problems.append('resync_required')
            rows[source] = {'digest': state['digest'], 'epoch': event['epoch'],
                            'sequence': event['sequence'], 'observed_at': event['observed_at'], 'expires_at': event['expires_at'],
                            'producer_version': event['producer_version'], 'adapter': event['adapter'], 'evidence_class': event['evidence_class'],
                            'records': deepcopy(state['records']), 'issues': sorted(problems)}
            issues.extend(f'{source}:{issue}' for issue in problems)
        # Overlapping provider instance IDs are only a possible join. Refuse
        # conflicting descriptions instead of choosing a preferred source.
        seen = {}
        for source, state in rows.items():
            for name, record in state['records'].items():
                if record['kind'] != 'provider-instance':
                    continue
                if name in seen and seen[name][1] != record:
                    other = seen[name][0]
                    for origin in (source, other):
                        if 'source_conflict' not in rows[origin]['issues']:
                            rows[origin]['issues'].append('source_conflict')
                            issues.append(origin + ':source_conflict')
                else:
                    seen[name] = (source, record)
        if not rows:
            issues.append('topology_missing')
        result = {'schema': 'dimaggi-topology-snapshot/v1', 'tenant': self.tenant, 'cluster': self.cluster,
                  'sources': rows, 'issues': sorted(issues), 'execution_authorized': False}
        result['snapshot_id'] = _digest(result)
        return result


def changed_sources(before, after):
    need((before['tenant'],before['cluster']) == (after['tenant'],after['cluster']), 'incompatible topology scope')
    return sorted(k for k in before['sources'].keys() | after['sources'].keys()
                  if before['sources'].get(k) != after['sources'].get(k))
