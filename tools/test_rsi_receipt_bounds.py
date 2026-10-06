"""Admission/consumption boundary regression: synthetic bytes and local stores."""
import copy
import json

import pytest

import rsi_admission as admission
import rsi_records as records
from test_rsi_admission import bind, bind_plan, enc, fixture


def batch(count, row_id_length=0):
    manifest, payload, trust = fixture()
    template = payload['rows'][0]
    payload['rows'] = []
    for index in range(count):
        row = copy.deepcopy(template)
        row.update(row_id=f'row-{index}'.ljust(row_id_length, 'x'),
                   source_id=f'source-{index}', group=f'group-{index}')
        payload['rows'].append(row)
    trust['partition_plan']['partitions'][0]['members'] = [
        {key: row[key] for key in admission.MEMBERSHIP} for row in payload['rows']]
    manifest['partition']['row_ids'] = [row['row_id'] for row in payload['rows']]
    bind_plan(manifest, trust)
    bind(manifest, payload)
    return tuple(map(enc, (manifest, payload, trust)))


@pytest.fixture
def replay(tmp_path, monkeypatch):
    anchor = tmp_path / 'anchor.json'
    admission.provision_replay_store(tmp_path / 'replay.db', anchor)
    monkeypatch.setenv('DIMAGGI_RSI_REPLAY_ANCHOR', str(anchor))


@pytest.mark.parametrize('count', [84, 85, 256])
def test_admitted_boundary_batches_consume_once(count, replay):
    args = batch(count)
    receipt = admission.admit_supplied_batch(*args)
    assert receipt['admission'] == 'accepted_static'
    assert len(receipt['checks']) == 2 + 3 * count
    # Formatting is irrelevant; canonical content still has to match exactly.
    receipt_bytes = json.dumps(receipt, indent=2).encode()
    assert len(admission.consume_supplied_batch(*args, receipt_bytes)) == count
    with pytest.raises(ValueError, match='cross_call_replay'):
        admission.consume_supplied_batch(*args, receipt_bytes)


def test_257_rows_still_refused(replay):
    args = batch(257)
    receipt = admission.admit_supplied_batch(*args)
    assert receipt['admission'] == 'rejected'
    with pytest.raises(ValueError, match='consumer_admission_refused'):
        admission.consume_supplied_batch(*args, enc(receipt))


@pytest.mark.parametrize('count,row_id_length', [(85, 2048), (256, 512)])
def test_long_row_ids_round_trip_with_receipt_only_byte_budget(count, row_id_length, replay):
    args = batch(count, row_id_length)
    assert all(len(raw) <= records.MAX_BYTES for raw in args)
    receipt = admission.admit_supplied_batch(*args)
    assert receipt['admission'] == 'accepted_static'
    raw = enc(receipt)
    assert len(raw) > records.MAX_BYTES
    with pytest.raises(ValueError, match='byte limit'):
        admission.consume_supplied_batch(*args, raw + b' ')
    assert len(admission.consume_supplied_batch(*args, raw)) == count


@pytest.mark.parametrize('mutation', ['extra_check', 'nested_array', 'other_array',
                                     'changed_check', 'duplicate_key', 'nonfinite', 'oversize'])
def test_malformed_or_tampered_receipt_does_not_claim_rows(mutation, replay):
    args = batch(85)
    original = admission.admit_supplied_batch(*args)
    receipt = copy.deepcopy(original)
    if mutation == 'extra_check':
        receipt['checks'].append(copy.deepcopy(receipt['checks'][-1]))
    elif mutation == 'nested_array':
        receipt['checks'][0]['checks'] = [0] * 257
    elif mutation == 'other_array':
        receipt['failing_row_ids'] = ['row'] * 257
    elif mutation == 'changed_check':
        receipt['checks'][-1]['status'] = 'failed'
    raw = enc(receipt)
    if mutation == 'duplicate_key':
        raw = b'{"admission":"accepted_static",' + raw[1:]
    elif mutation == 'nonfinite':
        raw = raw[:-1] + b',"extra":NaN}'
    elif mutation == 'oversize':
        raw += b' ' * records.MAX_BYTES
    reason = '256 entries' if mutation in {'nested_array', 'other_array'} else None
    with pytest.raises(ValueError, match=reason):
        admission.consume_supplied_batch(*args, raw)
    assert len(admission.consume_supplied_batch(*args, enc(original))) == 85


def test_generic_loader_keeps_256_entry_cap():
    with pytest.raises(ValueError, match='256 entries'):
        records.load(enc({'checks': [0] * 257}))
    with pytest.raises(ValueError, match='byte limit'):
        records.load(b'{}' + b' ' * records.MAX_BYTES)
