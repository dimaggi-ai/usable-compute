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
