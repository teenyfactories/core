"""Disposable PostgreSQL/PgBouncer proof; runner supplies only local fixture credentials."""
import os
import uuid

import pytest

from teenyfactories import config, db
from teenyfactories.collection import collection
from teenyfactories.datastore import get_datastore
import teenyfactories as tf

pytestmark = pytest.mark.skipif(
    os.getenv('POSTGRES_HOST') != '127.0.0.1' or os.getenv('POSTGRES_DB') != 'pooling_factory',
    reason='requires the disposable local pooling harness',
)


def test_one_backend_transaction_scope_and_rollback(monkeypatch):
    key = uuid.uuid4().hex
    pids = []
    for factory in ['canary_a', 'canary_b', 'canary_a', 'canary_b']:
        monkeypatch.setattr(config, 'FACTORY_NAME', factory)
        collection('probe').set(key, state='ready', data={'owner': factory})
        assert collection('probe').get(key)['data']['owner'] == factory
        with db.transaction() as cur:
            cur.execute("SELECT pg_backend_pid(), current_setting('app.factory_name')")
            pid, scope = cur.fetchone()
            pids.append(pid)
            assert scope == factory
    assert len(set(pids)) == 1, 'pool-size-one clients must reuse the same PostgreSQL backend'
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary_a')
    with pytest.raises(RuntimeError, match='intentional'):
        with db.transaction() as cur:
            cur.execute("UPDATE factory_data SET value='{}'::jsonb WHERE collection='probe' AND key=%s", (key,))
            raise RuntimeError('intentional rollback')
    assert collection('probe').get(key)['data']['owner'] == 'canary_a'
    # An unscoped transaction on the reused server must see no factory rows.
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM factory_data WHERE collection='probe' AND key=%s", (key,))
        assert cur.fetchone()[0] == 0
    with db.transaction() as cur:
        cur.execute('SELECT 1')
        assert cur.fetchone()[0] == 1


def test_vector_replace_rolls_back_delete_and_preserves_query_semantics():
    key = uuid.uuid4().hex
    rows = collection('vectors')
    rows.set(key, state='ready', data={'size': 10})
    assert rows.set_vectors(key, [{'content': 'original', 'embedding': [1.0] * 256}]) == 1
    with pytest.raises(Exception):
        rows.set_vectors(key, [{'content': 'invalid', 'embedding': [float('nan')] * 256}])
    found = rows.state('ready').where('size >= 10').vector_search([1.0] * 256).key(key).run()
    assert len(found) == 1
    assert found[0]['chunk']['content'] == 'original'
    assert rows.where('size >= 10').count() >= 1
    assert rows.set_vectors(key, []) == 0
    assert rows.vector_search([1.0] * 256).key(key).run() == []
    rows.remove(key)


def test_telemetry_breakpoints_and_usage_use_scoped_transactions(monkeypatch):
    store = get_datastore()
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary_a')
    log_id = store.append_log('probe', 'fixture', 'breakpoint', 'disposable', {'_debug': {'state': 'waiting'}})
    assert store.read_log(log_id)['_debug']['state'] == 'waiting'
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary_b')
    assert store.read_log(log_id) is None
    usage_id = store.record_usage(('canary_b', 'probe', None, 'llm', 'fixture', 'no-paid-model', '{}', None, None, 1))
    assert usage_id is not None


def test_reconnect_reapplies_factory_scope(monkeypatch):
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary_a')
    db.get_connection().close()
    with db.transaction() as cur:
        cur.execute("SELECT current_setting('app.factory_name')")
        assert cur.fetchone()[0] == 'canary_a'


def test_named_cross_factory_crud_vectors_and_claimed_handoff(monkeypatch):
    """Real restricted-role RLS, SQL queries and dispatch; no owner-role shortcut."""
    from teenyfactories.message_queue import base
    import teenyfactories.notifications as notifications
    from unittest.mock import Mock

    key = uuid.uuid4().hex
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary')
    monkeypatch.setattr(base, 'FACTORY_NAME', 'canary')
    monkeypatch.setattr(base, '_handlers', {})
    monkeypatch.setattr(base, '_pending_registrations', [])
    monkeypatch.setattr(base, '_strikes', {})
    monkeypatch.setattr(base, '_ran_keys', set())
    monkeypatch.setattr(base, '_park_reason', {})
    monkeypatch.setattr(base, '_provider_instance', None)
    monkeypatch.setattr(notifications, 'get_notifications', lambda: Mock())
    local = tf.collection('scope_probe')
    owner = tf.factory('canary_a').collection('scope_probe')
    other = tf.factory('canary_b').collection('scope_probe')
    created = None
    try:
        local.set(key, state='ready', data={'owner': 'local'})
        owner.set(key, state='ready', data={'owner': 'source', 'n': 10})
        other.set(key, state='ready', data={'owner': 'other'})
        assert local.get(key)['data']['owner'] == 'local'
        assert other.get(key)['data']['owner'] == 'other'
        assert owner.exists(key)
        created = owner.add('new', {'owner': 'new'})
        assert owner.get(created)['factory_name'] == 'canary_a'
        assert owner.where("owner == 'source'").state('ready').count() == 1
        assert owner.where("owner == 'source'").first()['key'] == key
        assert owner.set_vectors(key, [{'content': 'scoped', 'embedding': [1.0] * 256}]) == 1
        hits = owner.vector_search([1.0] * 256).key(key).run()
        assert hits[0]['factory_name'] == 'canary_a'
        assert not other.vector_search([1.0] * 256).key(key).run()
        owner.set(key, data={'owner': 'source'})
        assert 'n' not in owner.get(key)['data']  # Python replacement semantics survive.
        seen = []
        def handle(item):
            seen.append(item['factory_name'])
            assert local.get(key)['data']['owner'] == 'local'
            tf.factory(item['factory_name']).collection(item['collection']).set(item['key'], state='done')
        tf.factory('canary_a').on_state('scope_probe', 'ready').do(handle)
        base._flush_registrations()
        base._poll_pass()
        assert seen == ['canary_a']
        assert owner.get(key)['state'] == 'done'
        assert local.get(key)['state'] == other.get(key)['state'] == 'ready'
        with db.transaction('canary_a') as cur:
            cur.execute("SELECT count(*) FROM factory_job_claims WHERE claim_data->>'key'=%s", (key,))
            assert cur.fetchone()[0] == 0
        assert owner.set_vectors(key, []) == 0
    finally:
        for rows in [local, owner, other]:
            rows.remove(key)
        if created:
            owner.remove(created)
