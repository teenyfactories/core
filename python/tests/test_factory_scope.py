"""Factory-first addressing preserves owner identity without changing agent identity."""
import importlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import teenyfactories as tf
from teenyfactories import config, db, claims
from teenyfactories.datastore import get_datastore
from teenyfactories.datastore.postgres import PostgresDatastore
from teenyfactories.message_queue import base


@pytest.mark.parametrize('name', ['all', '', 'Bad', '../owner', 'x' * 31, None, 1])
def test_invalid_or_all_scope_is_rejected_before_io(name):
    with pytest.raises(ValueError):
        tf.factory(name)


def test_scope_reaches_every_collection_operation_and_query_clone(monkeypatch):
    module = importlib.import_module('teenyfactories.collection')
    query_module = importlib.import_module('teenyfactories.query')
    local, remote = Mock(), Mock()
    remote.query.return_value = [{'factory_name': 'owner', 'collection': 'person', 'key': 'same'}]
    remote.count.return_value = 1
    select = lambda name=None: local if name is None else remote
    monkeypatch.setattr(module, 'get_datastore', select)
    monkeypatch.setattr(query_module, 'get_datastore', select)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'caller')
    people = tf.factory('owner').collection('person')
    people.get('same')
    people.exists('same')
    people.set('same', state='verified')
    people.set('same', data={'name': 'Alex'})
    people.add('registered', {'name': 'Alex'})
    people.set_vectors('same', [{'embedding': [0.0] * 256, 'content': 'Alex'}])
    people.remove('same')
    assert people.state('verified').where("name == 'Alex'").limit(2).first()['factory_name'] == 'owner'
    assert people.count() == 1
    query = people.vector_search([0.0] * 256).key('same').chunks().limit(2)
    assert query._factory_name == 'owner'
    assert list(query)[0]['factory_name'] == 'owner'
    remote.get.assert_called_once_with('person', 'same')
    remote.exists.assert_called_once_with('person', 'same')
    assert remote.upsert.call_args_list[0].args == ('person', 'same', 'verified', None)
    assert remote.upsert.call_args_list[1].args == ('person', 'same', None, {'name': 'Alex'})
    remote.remove.assert_called_once_with('person', 'same')
    assert remote.insert.call_args.args[:1] == ('person',)
    remote.replace_vectors.assert_called_once()
    tf.collection('person').get('same')
    local.get.assert_called_once_with('person', 'same')
    assert config.FACTORY_NAME == 'caller'


def test_named_sql_and_rls_scope_are_transaction_local(monkeypatch):
    cursor = Mock()
    connection = Mock(closed=False)
    connection.get_transaction_status.return_value = 0
    connection.cursor.return_value = cursor
    cursor.fetchone.return_value = ('owner', 'person', 'same', 'system', {'name': 'Alex'}, 'new', None, None)
    monkeypatch.setattr(db, 'get_connection', lambda: connection)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'caller')
    assert tf.factory('owner').collection('person').get('same')['factory_name'] == 'owner'
    calls = cursor.execute.call_args_list
    assert calls[1].args == ("SELECT set_config('app.factory_name', %s, true)", ('owner',))
    assert calls[2].args[1] == ('owner', 'person', 'same')
    assert calls[-1].args == ('COMMIT',)
    tf.collection('person').get('same')
    assert cursor.execute.call_args_list[-3].args[1] == ('caller',)
    assert config.FACTORY_NAME == 'caller'


def test_every_named_sql_path_uses_target_transaction(monkeypatch):
    cursor = Mock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    scopes = []
    @contextmanager
    def transaction(source=None):
        scopes.append(source)
        yield cursor
    monkeypatch.setattr(db, 'transaction', transaction)
    people = tf.factory('owner').collection('person')
    people.add('new', {})
    people.set('same', state='ready')
    people.remove('same')
    people.get('same')
    people.exists('same')
    people.set_vectors('same', [{'embedding': [0.0] * 256}])
    people.where('is_active == true').get_all()
    people.vector_search([0.0] * 256).run()
    cursor.fetchone.return_value = (2,)
    assert people.count() == 2
    assert scopes == ['owner'] * 9
    assert all('owner' in call.args[1] for call in cursor.execute.call_args_list)


def test_failed_named_transaction_rolls_back_without_changing_local_scope(monkeypatch):
    cursor = Mock()
    connection = Mock(closed=False)
    connection.get_transaction_status.return_value = 0
    connection.cursor.return_value = cursor
    monkeypatch.setattr(db, 'get_connection', lambda: connection)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'caller')
    with pytest.raises(RuntimeError, match='intentional'):
        with db.transaction('owner'):
            raise RuntimeError('intentional')
    assert cursor.execute.call_args.args == ('ROLLBACK',)
    with db.transaction():
        pass
    assert cursor.execute.call_args_list[-2].args[1] == ('caller',)
    assert config.FACTORY_NAME == 'caller'


def test_subscription_registration_and_claims_keep_source(monkeypatch):
    monkeypatch.setattr(base, '_pending_registrations', [])
    monkeypatch.setattr(base, '_handlers', {})
    monkeypatch.setattr(base, '_strikes', {})
    monkeypatch.setattr(base, '_ran_keys', set())
    monkeypatch.setattr(base, '_park_reason', {})
    monkeypatch.setattr(base, 'FACTORY_NAME', 'caller')
    notifications = Mock()
    monkeypatch.setattr(importlib.import_module('teenyfactories.notifications'), 'get_notifications', lambda: notifications)
    acquired, released = Mock(return_value=True), Mock()
    monkeypatch.setattr(claims, 'try_claim', acquired)
    monkeypatch.setattr(claims, 'release_claim', released)
    monkeypatch.setattr(importlib.import_module('teenyfactories.breakpoint'), '_auto_halt', lambda *a: None)
    handler = Mock()
    tf.factory('owner').on_state('person', 'new').delay(seconds=2).priority(-1).claim_duration(minutes=5).do(handler)
    tf.on_state('person', 'new').do(lambda item: None)
    base._flush_registrations()
    assert set(base._handlers) == {('owner', 'person', 'new'), ('caller', 'person', 'new')}
    notifications.update_subscriptions.assert_called_once_with(list(base._handlers))
    row = {'factory_name': 'owner', 'collection': 'person', 'key': 'same',
           'state': 'new', 'state_changed_at': datetime.now(timezone.utc)}
    base._dispatch(base._handlers[('owner', 'person', 'new')], row)
    handler.assert_called_once_with(row)
    assert acquired.call_args.kwargs == {'factory_name': 'owner'}
    assert acquired.call_args.args[-1] == 300
    assert released.call_args.kwargs == {'factory_name': 'owner'}
    for other in [{**row, 'factory_name': 'caller'}, {**row, 'collection': 'organisation'}]:
        assert base._strike_key(row) != base._strike_key(other)
    assert claims.hash_claim_key('person', 'same', 'new', row['state_changed_at'], 'owner') != \
        claims.hash_claim_key('person', 'same', 'new', row['state_changed_at'], 'caller')


def test_poll_compares_named_heads_and_preserves_qualified_exclusions(monkeypatch):
    timestamp = datetime.now(timezone.utc)
    make_row = lambda name, priority: {'factory_name': name, 'key': 'same', '_tier': 1,
                                    '_priority': priority, 'state_changed_at': timestamp}
    stores = {'one': Mock(), 'two': Mock()}
    stores['one']._peek_local.return_value = make_row('one', 5)
    stores['two']._peek_local.return_value = make_row('two', -1)
    monkeypatch.setattr(importlib.import_module('teenyfactories.datastore'), 'get_datastore', stores.__getitem__)
    excluded = [('one', 'person', 'same', 'new', timestamp)]
    result = PostgresDatastore().peek_next([
        ('one', 'person', 'new', 1, 5, 0), ('two', 'person', 'new', 1, -1, 0)
    ], 10, excluded)
    assert result['factory_name'] == 'two'
    assert stores['one']._peek_local.call_args.args[-1] == [excluded[0][1:]]
    assert stores['two']._peek_local.call_args.args[-1] == []


def test_named_claim_sql_uses_source_hash_and_scope(monkeypatch):
    cursor = Mock()
    cursor.fetchone.return_value = ('claimed',)
    sources = []
    @contextmanager
    def transaction(source=None):
        sources.append(source)
        yield cursor
    monkeypatch.setattr(db, 'transaction', transaction)
    store = get_datastore('owner')
    timestamp = datetime.now(timezone.utc)
    assert store.try_claim('person', 'same', 'new', timestamp, 60)
    params = cursor.execute.call_args.args[1]
    assert params[0] == params[5] == 'owner'
    assert params[6] == claims.hash_claim_key('person', 'same', 'new', timestamp, 'owner')
    store.release_claim('person', 'same', 'new', timestamp)
    assert cursor.execute.call_args.args[1][0] == 'owner'
    assert sources == ['owner', 'owner']


def test_janitor_reaps_only_local_and_subscribed_sources(monkeypatch):
    stores = {'caller': Mock(), 'owner': Mock()}
    for store in stores.values():
        store.reap_claims.return_value = []
    monkeypatch.setattr(config, 'FACTORY_NAME', 'caller')
    monkeypatch.setattr(claims, '_last_janitor_tick', 0)
    monkeypatch.setattr(claims.time, 'monotonic', lambda: 100)
    monkeypatch.setattr(claims, 'get_datastore', lambda name=None: stores[name or 'caller'])
    claims.janitor_sweep_if_due({'caller', 'owner'})
    assert all(store.reap_claims.call_count == 1 for store in stores.values())


def test_direct_wakes_include_named_sources(monkeypatch):
    from teenyfactories.notifications.postgres import PostgresNotifications
    notices = [SimpleNamespace(payload='{"factory_name":"owner"}')]
    connection = MagicMock()
    connection.notifies = notices
    @contextmanager
    def session():
        yield connection
    monkeypatch.setattr(db, 'session', session)
    notification = PostgresNotifications()
    notification.update_subscriptions([('owner', 'person', 'new')])
    assert notification.consume_wake() is True
    notices.append(SimpleNamespace(payload='{"factory_name":"unrelated"}'))
    assert notification.consume_wake() is False
