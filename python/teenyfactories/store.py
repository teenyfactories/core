"""
Factory Data Store

Unified key-value store for factory-specific persistent data. Backed by the
`factory_data` table. Every row has a `state` that drives pub/sub — workers
subscribe to state transitions via `tf.on_state(collection, state)`.

Embeddings live in a companion `factory_vectors` table, keyed 1:1 with
factory_data rows. The dim column is auto-selected from the embedding length.

Usage:
    import teenyfactories as tf

    tf.store('preferences').set('key1', {'sentiment': 'up'})
    tf.store('documents').set(key='doc.pdf', value={...}, state='loaded')
    tf.store('documents').find(state='loaded')    # list of keys
    tf.store('chunks').set(None, chunk_val, embedding=[...])  # auto-UUID key
    tf.store('preferences').get('key1')
    tf.store('preferences').all()
    tf.store('preferences').delete('key1')
"""

import os
import json
import re
import uuid
from typing import Optional, List, Dict, Any, Union

from .logging import log_error

_SUPPORTED_DIMS = (256, 512, 768, 1024, 1536, 3072)
_ID_RE = re.compile(r'^[a-z0-9_]+$')
_MAX_STATE_LEN = 40
_MAX_COLLECTION_LEN = 40
_MAX_CHANNEL_LEN = 63  # Postgres identifier limit


def _get_connection():
    """Get a database connection using the provider's connection."""
    from .message_queue.base import _get_provider
    provider = _get_provider()
    return provider.cursor, os.getenv('FACTORY_PREFIX', '')


def _validate_collection(collection: str):
    if not collection or not _ID_RE.match(collection) or len(collection) > _MAX_COLLECTION_LEN:
        raise ValueError(
            f"Invalid collection name {collection!r}: must match [a-z0-9_]+ and be <= {_MAX_COLLECTION_LEN} chars"
        )


def _validate_state(state: str):
    if not state or not _ID_RE.match(state) or len(state) > _MAX_STATE_LEN:
        raise ValueError(
            f"Invalid state {state!r}: must match [a-z0-9_]+ and be <= {_MAX_STATE_LEN} chars"
        )


def _check_channel_length(factory_name: str, collection: str, state: str):
    length = len(factory_name) + 1 + len(collection) + 1 + len(state)
    if length > _MAX_CHANNEL_LEN:
        raise ValueError(
            f"NOTIFY channel would exceed {_MAX_CHANNEL_LEN} chars "
            f"(factory={factory_name!r} collection={collection!r} state={state!r} -> {length} chars)"
        )


def _dim_column(embedding: list) -> str:
    dim = len(embedding)
    if dim not in _SUPPORTED_DIMS:
        raise ValueError(
            f"Unsupported embedding dim {dim}; must be one of {_SUPPORTED_DIMS}"
        )
    return f"embedding_{dim}"


def _row_to_item(row, columns) -> Dict[str, Any]:
    """Turn a DB row into an item dict matching the on_state handler contract."""
    item = dict(zip(columns, row))
    if 'value' in item and not isinstance(item['value'], dict):
        try:
            item['value'] = json.loads(item['value'])
        except Exception:
            pass
    return item


class StoreCollection:
    """Fluent interface for a named collection within factory_data."""

    def __init__(self, collection: str):
        _validate_collection(collection)
        self._collection = collection

    def set(
        self,
        key: Optional[str],
        value: dict,
        state: str = 'new',
        user_id: str = 'system',
        embedding: Optional[list] = None,
    ) -> str:
        """Upsert a value by key. Returns the key (generated as UUID if key=None).

        - state:   pub/sub state (default 'new'). Must match [a-z0-9_]+.
        - user_id: who made this change ('system' for agents, session id for API).
        - embedding: optional vector. Routed to factory_vectors into the
          matching embedding_{dim} column. Raises if dim unsupported.
        """
        _validate_state(state)
        if key is None:
            key = uuid.uuid4().hex

        try:
            cursor, factory_name = _get_connection()
            _check_channel_length(factory_name, self._collection, state)

            cursor.execute(
                """INSERT INTO factory_data (factory_name, collection, key, user_id, value, state)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (factory_name, collection, key)
                   DO UPDATE SET
                       value = EXCLUDED.value,
                       state = EXCLUDED.state,
                       user_id = EXCLUDED.user_id""",
                (factory_name, self._collection, key, user_id, json.dumps(value), state)
            )

            if embedding is not None:
                col = _dim_column(embedding)
                # Upsert into factory_vectors — set the matching dim column, null others implicitly
                cursor.execute(
                    f"""INSERT INTO factory_vectors (factory_name, collection, key, {col})
                        VALUES (%s, %s, %s, %s::vector)
                        ON CONFLICT (factory_name, collection, key)
                        DO UPDATE SET {col} = EXCLUDED.{col}""",
                    (factory_name, self._collection, key, str(embedding))
                )
            return key
        except Exception as e:
            log_error(f"store.set failed: {e}")
            raise

    def set_state(self, key: str, state: str, user_id: str = 'system'):
        """Update only the state of an existing row. Fires state-change NOTIFY."""
        _validate_state(state)
        try:
            cursor, factory_name = _get_connection()
            _check_channel_length(factory_name, self._collection, state)
            cursor.execute(
                """UPDATE factory_data
                   SET state = %s, user_id = %s
                   WHERE factory_name = %s AND collection = %s AND key = %s""",
                (state, user_id, factory_name, self._collection, key)
            )
        except Exception as e:
            log_error(f"store.set_state failed: {e}")
            raise

    def get(self, key: str) -> Optional[dict]:
        """Get a value by key. Returns the value dict (not the full item) or None."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT value FROM factory_data WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._collection, key)
            )
            row = cursor.fetchone()
            if row:
                return row[0] if isinstance(row[0], dict) else json.loads(row[0])
            return None
        except Exception as e:
            log_error(f"store.get failed: {e}")
            return None

    def get_item(self, key: str) -> Optional[Dict[str, Any]]:
        """Get the full item (value + state + user_id + timestamps) or None."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state, created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s AND collection = %s AND key = %s""",
                (factory_name, self._collection, key)
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = ['factory_name', 'collection', 'key', 'user_id', 'value', 'state', 'created_at', 'updated_at']
            return _row_to_item(row, cols)
        except Exception as e:
            log_error(f"store.get_item failed: {e}")
            return None

    def all(self) -> List[Dict[str, Any]]:
        """Get all items in the collection. Returns list of {key, value, state}."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT key, value, state FROM factory_data WHERE factory_name = %s AND collection = %s ORDER BY updated_at DESC",
                (factory_name, self._collection)
            )
            rows = cursor.fetchall()
            return [
                {
                    'key': row[0],
                    'value': row[1] if isinstance(row[1], dict) else json.loads(row[1]),
                    'state': row[2],
                }
                for row in rows
            ]
        except Exception as e:
            log_error(f"store.all failed: {e}")
            return []

    def keys(self) -> List[str]:
        """Get all keys in the collection (no values loaded)."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT key FROM factory_data WHERE factory_name = %s AND collection = %s ORDER BY updated_at DESC",
                (factory_name, self._collection)
            )
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            log_error(f"store.keys failed: {e}")
            return []

    def find(self, state: Optional[str] = None, field: Optional[str] = None, value: Optional[str] = None) -> List[str]:
        """Find keys matching a filter. Returns list of keys.

        Usage:
            find(state='loaded')          — by state column (preferred)
            find(field='document', value='x.pdf')  — by JSONB field (legacy)
        """
        try:
            cursor, factory_name = _get_connection()
            if state is not None:
                _validate_state(state)
                cursor.execute(
                    """SELECT key FROM factory_data
                       WHERE factory_name = %s AND collection = %s AND state = %s
                       ORDER BY updated_at DESC""",
                    (factory_name, self._collection, state)
                )
            elif field is not None:
                cursor.execute(
                    """SELECT key FROM factory_data
                       WHERE factory_name = %s AND collection = %s AND value->>%s = %s
                       ORDER BY updated_at DESC""",
                    (factory_name, self._collection, field, value)
                )
            else:
                raise ValueError("find() requires either state= or field=+value=")
            return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            log_error(f"store.find failed: {e}")
            return []

    def find_items(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """Like find(state=X) but returns full item dicts instead of just keys."""
        try:
            cursor, factory_name = _get_connection()
            if state is None:
                raise ValueError("find_items currently requires state=")
            _validate_state(state)
            cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state, created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s AND collection = %s AND state = %s
                   ORDER BY updated_at DESC""",
                (factory_name, self._collection, state)
            )
            cols = ['factory_name', 'collection', 'key', 'user_id', 'value', 'state', 'created_at', 'updated_at']
            return [_row_to_item(row, cols) for row in cursor.fetchall()]
        except Exception as e:
            log_error(f"store.find_items failed: {e}")
            return []

    def search(self, embedding: list, limit: int = 5,
               filter_field: Optional[str] = None, filter_value: Optional[str] = None) -> List[Dict[str, Any]]:
        """Semantic similarity search. Joins factory_vectors + factory_data.
        Returns list of {key, value, similarity, state}."""
        try:
            cursor, factory_name = _get_connection()
            col = _dim_column(embedding)
            base = f"""
                SELECT d.key, d.value, d.state,
                       1 - (v.{col} <=> %s::vector) AS similarity
                  FROM factory_vectors v
                  JOIN factory_data d
                    ON d.factory_name = v.factory_name
                   AND d.collection  = v.collection
                   AND d.key         = v.key
                 WHERE v.factory_name = %s
                   AND v.collection  = %s
                   AND v.{col} IS NOT NULL
            """
            params = [str(embedding), factory_name, self._collection]
            if filter_field is not None and filter_value is not None:
                base += " AND d.value->>%s = %s"
                params.extend([filter_field, filter_value])
            base += f" ORDER BY v.{col} <=> %s::vector LIMIT %s"
            params.extend([str(embedding), limit])

            cursor.execute(base, params)
            rows = cursor.fetchall()
            return [
                {
                    'key':   row[0],
                    'value': row[1] if isinstance(row[1], dict) else json.loads(row[1]),
                    'state': row[2],
                    'similarity': float(row[3]),
                }
                for row in rows
            ]
        except Exception as e:
            log_error(f"store.search failed: {e}")
            return []

    def delete(self, key: str):
        """Delete a value by key. Cascades to factory_vectors."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "DELETE FROM factory_data WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._collection, key)
            )
        except Exception as e:
            log_error(f"store.delete failed: {e}")
            raise


def store(collection: str) -> StoreCollection:
    """Access a named data collection for this factory."""
    return StoreCollection(collection)
