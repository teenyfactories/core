"""Transaction setup and failures must never leak scope or a poisoned connection."""
import importlib
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from teenyfactories import config, db
from teenyfactories.collection import collection
from teenyfactories.query import CollectionQuery


def connection(monkeypatch, fail=None):
    cur = Mock()
    commands = []
    def execute(sql, params=None):
        commands.append((sql, params))
        if sql == fail:
            raise ValueError('fixture failure')
    cur.execute.side_effect = execute
    conn = Mock(closed=False)
    conn.get_transaction_status.return_value = 0
    conn.cursor.return_value = cur
    monkeypatch.setattr(db, 'get_connection', lambda: conn)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary')
    return conn, cur, commands


def test_setup_and_commit_scoped_atomically(monkeypatch):
    conn, cur, commands = connection(monkeypatch)
    with db.transaction() as actual:
        assert actual is cur
        actual.execute('operation')
    assert commands == [('BEGIN', None), ("SELECT set_config('app.factory_name', %s, true)", ('canary',)),
                        ('operation', None), ('COMMIT', None)]
    cur.close.assert_called_once()
    conn.close.assert_not_called()


@pytest.mark.parametrize('failed', ['BEGIN', "SELECT set_config('app.factory_name', %s, true)", 'operation', 'COMMIT'])
def test_every_failure_rolls_back_before_reuse(monkeypatch, failed):
    conn, cur, commands = connection(monkeypatch, failed)
    with pytest.raises(ValueError, match='fixture failure'):
        with db.transaction() as actual:
            actual.execute('operation')
    assert commands[-1][0] == 'ROLLBACK'
    cur.close.assert_called_once()


def test_failed_rollback_discards_connection(monkeypatch):
    conn, cur, _ = connection(monkeypatch)
    cur.execute.side_effect = ValueError('broken session')
    with pytest.raises(ValueError):
        with db.transaction():
            pytest.fail('operation must not run when BEGIN failed')
    conn.close.assert_called_once()


def test_nested_transactions_are_rejected_without_committing_outer_work(monkeypatch):
    conn, cur, commands = connection(monkeypatch)
    with pytest.raises(RuntimeError, match='Nested'):
        with db.transaction():
            conn.get_transaction_status.return_value = 2
            with db.transaction():
                pytest.fail('nested transaction entered')
    assert [command for command, _ in commands] == [
        'BEGIN', "SELECT set_config('app.factory_name', %s, true)", 'ROLLBACK']


def test_complete_transactions_are_serialized_between_threads(monkeypatch):
    _, _, commands = connection(monkeypatch)
    waiting, entered = Event(), Event()
    failures = []

    def worker():
        waiting.set()
        try:
            with db.transaction() as cur:
                entered.set()
                cur.execute('second')
        except BaseException as exc:
            failures.append(exc)

    with db.transaction() as cur:
        thread = Thread(target=worker)
        thread.start()
        assert waiting.wait(1)
        assert not entered.wait(0.05)
        cur.execute('first')
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not failures
    assert [command for command, _ in commands] == [
        'BEGIN', "SELECT set_config('app.factory_name', %s, true)", 'first', 'COMMIT',
        'BEGIN', "SELECT set_config('app.factory_name', %s, true)", 'second', 'COMMIT']


@pytest.mark.parametrize(('setting', 'mode'), [('true', 'require'), ('false', 'disable'),
                                           ('verify-full', 'verify-full'), ('', 'prefer')])
def test_factory_connection_preserves_selected_tls_mode(monkeypatch, setting, mode):
    import psycopg2
    connect = Mock(return_value=Mock())
    monkeypatch.setattr(psycopg2, 'connect', connect)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary')
    monkeypatch.setattr(config, 'POSTGRES_SSL', setting)
    config.connect_postgres()
    assert connect.call_args.kwargs['sslmode'] == mode


def test_semantic_collection_and_query_do_not_expose_sql(monkeypatch):
    store = Mock()
    store.upsert.return_value = 'key'
    store.query.return_value = [{'key': 'key', 'data': {'n': 2}}]
    monkeypatch.setattr(importlib.import_module('teenyfactories.collection'), 'get_datastore', lambda: store)
    monkeypatch.setattr(importlib.import_module('teenyfactories.query'), 'get_datastore', lambda: store)
    monkeypatch.setattr(config, 'FACTORY_NAME', 'canary')
    assert collection('probe').set('key', state='ready', data={'n': 2}) == 'key'
    store.upsert.assert_called_once_with('probe', 'key', 'ready', {'n': 2})
    query = CollectionQuery('probe').state('ready').where('n >= 2')
    assert query.key(k='fixed')._vec_key == 'fixed'
    assert query._wheres[0][0] == 'cmp'
    assert query.run()[0]['key'] == 'key'
    store.query.assert_called_once_with(query)
