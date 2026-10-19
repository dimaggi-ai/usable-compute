from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import json
import math
import re
import time
from urllib.parse import urlsplit


SCHEMA_VERSION = 'effect-observation/v1'
MAX_BYTES = 1024 * 1024
MAX_EFFECTS = 4096


@dataclass(frozen=True, order=True)
class EffectReceipt:
    operation_id: str
    input_sha256: str
    destination: str


@dataclass(frozen=True)
class Observation:
    effects: tuple[EffectReceipt, ...]


@dataclass(frozen=True)
class Verification:
    complete: bool
    observed_effects: int
    findings: tuple[str, ...]


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _validate(observation: Observation) -> dict[str, EffectReceipt]:
    if type(observation) is not Observation or type(observation.effects) is not tuple:
        raise ValueError('invalid_observation')
    if len(observation.effects) > MAX_EFFECTS:
        raise ValueError('too_many_effects')
    indexed = {}
    for receipt in observation.effects:
        if type(receipt) is not EffectReceipt:
            raise ValueError('invalid_receipt')
        for value in (receipt.operation_id, receipt.destination):
            if type(value) is not str or not value or len(value) > 1024:
                raise ValueError('invalid_binding')
        if type(receipt.input_sha256) is not str or re.fullmatch('[0-9a-f]{64}', receipt.input_sha256) is None:
            raise ValueError('invalid_digest')
        if receipt.operation_id in indexed:
            raise ValueError('duplicate_operation')
        indexed[receipt.operation_id] = receipt
    return indexed


def parse_snapshot(raw: bytes) -> Observation:
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError('invalid_snapshot_size')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid_json') from exc
    if type(value) is not dict or set(value) != {'schema_version', 'effects'}:
        raise ValueError('invalid_snapshot_fields')
    if value['schema_version'] != SCHEMA_VERSION or type(value['effects']) is not list:
        raise ValueError('invalid_snapshot_schema')
    effects = []
    if len(value['effects']) > MAX_EFFECTS:
        raise ValueError('too_many_effects')
    for item in value['effects']:
        if type(item) is not dict or set(item) != {'operation_id', 'input_sha256', 'destination'}:
            raise ValueError('invalid_receipt_fields')
        effects.append(EffectReceipt(**item))
    observation = Observation(tuple(effects))
    _validate(observation)
    return observation


def collect(endpoint: str, *, timeout: float = 2.0) -> Observation:
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError('invalid_timeout')
    address = urlsplit(endpoint)
    if (address.scheme != 'http' or address.path != '/observations' or address.query
            or address.fragment or address.username is not None or address.password is not None):
        raise ValueError('invalid_endpoint')
    try:
        host = ipaddress.ip_address(address.hostname or '')
    except ValueError as exc:
        raise ValueError('literal_loopback_required') from exc
    if not host.is_loopback:
        raise ValueError('literal_loopback_required')
    connection = http.client.HTTPConnection(str(host), address.port, timeout=timeout)
    deadline = time.monotonic() + timeout
    response = None
    try:
        connection.request('GET', '/observations', headers={'Accept': 'application/json'})
        transport = connection.sock
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('observation_http_status')
        parts = []
        size = 0
        while not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('observation_deadline')
            transport.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            parts.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('invalid_snapshot_size')
        if response.length not in (None, 0):
            raise ValueError('incomplete_response')
        return parse_snapshot(b''.join(parts))
    finally:
        if response is not None:
            response.close()
        connection.close()


def verify(snapshot: Observation, expected: Observation, *, checkpoint: Observation | None = None) -> Verification:
    observed = _validate(snapshot)
    receipts = _validate(expected)
    retained = _validate(checkpoint) if checkpoint is not None else {}
    findings = []
    for operation_id, receipt in receipts.items():
        if operation_id not in observed:
            findings.append('missing_effect:' + operation_id)
        elif observed[operation_id] != receipt:
            findings.append('modified_effect:' + operation_id)
    for operation_id in observed.keys() - receipts.keys():
        findings.append('unexpected_effect:' + operation_id)
    for operation_id, receipt in retained.items():
        if operation_id not in observed:
            findings.append('checkpoint_omission:' + operation_id)
        elif observed[operation_id] != receipt:
            findings.append('checkpoint_modification:' + operation_id)
    return Verification(not findings, len(observed), tuple(sorted(findings)))
