import sqlite3
import pytest
from dimaggi_receiver.observations import ObservationError
from test_observations import store, event, T1, T3, FRESHNESS


@pytest.mark.parametrize('mutation', ['flip', 'state', 'delete', 'reorder'])
@pytest.mark.parametrize('read', ['history', 'project', 'reconcile'])
def test_every_read_refuses_event_tampering(store, mutation, read):
    store.append(event('workload', 'pending'), recorded_at_utc=T1)
    store.append(event('workload', 'running', 2), recorded_at_utc=T1)
    store.db.execute('DROP TRIGGER events_no_update')
    store.db.execute('DROP TRIGGER events_no_delete')
    if mutation == 'flip':
        store.db.execute("UPDATE events SET body=replace(body, 'uid-1', 'uid-0') WHERE position=1")
    elif mutation == 'state':
        store.db.execute("UPDATE events SET body=replace(body, 'pending', 'running') WHERE position=1")
    elif mutation == 'delete':
        store.db.execute('DELETE FROM events WHERE position=2')
    else:
        store.db.execute('UPDATE events SET position=position+100')
    store.db.commit()
    with pytest.raises(ObservationError, match='integrity'):
        if read == 'history': store.history('request-1')
        else: getattr(store, read)('request-1', as_of_utc=T3, freshness_seconds=FRESHNESS)


def test_chain_head_changes_and_survives_reopen(store):
    from dimaggi_receiver.observations import ObservationStore
    store.append(event('workload','pending'), recorded_at_utc=T1)
    head=store.chain_head()
    assert head['count']==1 and len(head['sha256'])==64
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    store.close()
    with ObservationStore(path, create=False) as reopened:
        assert reopened.chain_head()==head


def test_legacy_rows_are_explicitly_unverified(store):
    from dimaggi_receiver.observations import ObservationStore
    store.append(event('workload','pending'), recorded_at_utc=T1)
    path=store.db.execute('PRAGMA database_list').fetchone()[2]
    store.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE event_integrity')
        db.execute('DROP TABLE chain_state')
    with ObservationStore(path) as migrated:
        assert migrated.history('request-1')[0]['integrity']=='unverified-legacy'
        with pytest.raises(ObservationError, match='unverified-legacy'):
            migrated.project('request-1', as_of_utc=T3, freshness_seconds=FRESHNESS)


@pytest.mark.parametrize('attack', ['intent', 'conflict', 'triage', 'resolution'])
def test_decision_table_tampering_refused(store, attack):
    from test_observations import append_base, project
    from dimaggi_receiver.observations import SourceConflict
    append_base(store, workload='failed' if attack == 'intent' else 'succeeded')
    if attack == 'intent':
        assert project(store)['recommendation'] == 'hold'
        store.db.execute('DROP TRIGGER intents_no_update')
        store.db.execute("UPDATE intents SET body=replace(body, 'succeeded', 'present')")
    elif attack == 'conflict':
        with pytest.raises(SourceConflict):
            store.append(event('workload', 'failed'), recorded_at_utc=T1)
        assert project(store)['recommendation'] == 'escalate'
        store.db.execute('DROP TRIGGER conflicts_no_delete')
        store.db.execute('DELETE FROM conflicts')
    else:
        # These notes are read directly by the decision API as well as projection.
        store.db.execute(f"INSERT INTO {attack if attack == 'triage' else 'resolutions'} VALUES ('note','case',?, '{{}}')", (T1,))
        table = 'triage' if attack == 'triage' else 'resolutions'
        store.db.execute(f'DROP TRIGGER {table}_no_delete')
        store.db.execute(f'DELETE FROM {table}')
    store.db.commit()
    with pytest.raises(ObservationError, match='integrity'):
        if attack == 'triage': store.triage_history('case')
        elif attack == 'resolution': store.resolution_history('case')
        else: project(store)


def test_tail_rollback_requires_retained_external_count_and_head(store):
    store.append(event('workload','pending'), recorded_at_utc=T1)
    prior = store.db.execute('SELECT count,head FROM chain_state').fetchone()
    store.append(event('workload','running',2), recorded_at_utc=T1)
    anchor = store.chain_head()
    store.db.execute('DROP TRIGGER events_no_delete')
    store.db.execute('DELETE FROM events WHERE position=2')
    store.db.execute('DELETE FROM event_integrity WHERE position=2')
    store.db.execute('UPDATE chain_state SET count=?,head=?', tuple(prior))
    store.db.commit()
    assert store.chain_head()['count'] == 1
    with pytest.raises(ObservationError, match='external anchor'):
        store.chain_head(expected=anchor)


def test_missing_chain_state_has_explicit_integrity_refusal(store):
    from dimaggi_receiver.observations import ObservationStore
    store.append(event('workload','pending'), recorded_at_utc=T1)
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    store.db.execute('DELETE FROM chain_state'); store.db.commit(); store.close()
    with pytest.raises(ObservationError, match='integrity state missing'):
        ObservationStore(path)
