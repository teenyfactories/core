"""
Factory Collection API.

Every factory's persistent state lives in named collections inside the
`factory_data` table. Each row carries a `state` column that drives pub/sub
— workers subscribe to state transitions via `tf.on_state(coll, state)`.

Embeddings live in a companion `factory_vectors` table, keyed 1:1 with
factory_data rows. The dim column is auto-selected from the embedding
length.

Public API:

    tf.collection(name)
        .set(key, state=..., data=..., embedding=...)   # update existing row
        .add(state, data=..., embedding=...)            # create new row, auto-UUID
        .get(key)                                       # full row or None
        .get_all(state=...)                             # list of rows
        .remove(key)                                    # delete one
        .count(state=...)                               # int
        .exists(key)                                    # bool
        .vector_search(text_or_vec, limit=5, state=...) # ANN

Naming conventions used in returned row dicts:
    {factory_name, collection, key, user_id, data, state, created_at, updated_at}

`data` is the JSONB payload (DB column is named `value` for legacy reasons;
the Python surface uses `data` everywhere).
"""

import json
import re
import uuid
from typing import Optional, List, Dict, Any, Union

from . import config, db
from .logging import log_error

_SUPPORTED_DIMS = (256, 512, 768, 1024, 1536, 3072)
_ID_RE = re.compile(r'^[a-z0-9_]+$')
_MAX_STATE_LEN = 40
_MAX_COLLECTION_LEN = 40
_MAX_CHANNEL_LEN = 63  # Postgres identifier limit

_ROW_COLS = ['factory_name', 'collection', 'key', 'user_id', 'value', 'state', 'created_at', 'updated_at']


def _get_connection():
    """Fresh cursor on the process-wide shared connection (teenyfactories.db)."""
    return db.cursor(), config.FACTORY_NAME


def _validate_collection(name: str):
    if not name or not _ID_RE.match(name) or len(name) > _MAX_COLLECTION_LEN:
        raise ValueError(
            f"Invalid collection name {name!r}: must match [a-z0-9_]+ "
            f"and be <= {_MAX_COLLECTION_LEN} chars"
        )


def _validate_state(state: str):
    if not state or not _ID_RE.match(state) or len(state) > _MAX_STATE_LEN:
        raise ValueError(
            f"Invalid state {state!r}: must match [a-z0-9_]+ "
            f"and be <= {_MAX_STATE_LEN} chars"
        )


def _check_channel_length(factory_name: str, collection: str, state: str):
    length = len(factory_name) + 1 + len(collection) + 1 + len(state)
    if length > _MAX_CHANNEL_LEN:
        raise ValueError(
            f"NOTIFY channel would exceed {_MAX_CHANNEL_LEN} chars "
            f"(factory={factory_name!r} collection={collection!r} "
            f"state={state!r} -> {length} chars)"
        )


def _dim_column(embedding: list) -> str:
    dim = len(embedding)
    if dim not in _SUPPORTED_DIMS:
        raise ValueError(
            f"Unsupported embedding dim {dim}; must be one of {_SUPPORTED_DIMS}"
        )
    return f"embedding_{dim}"


def _row_to_dict(row) -> Dict[str, Any]:
    """
    Map a SELECT result tuple (in _ROW_COLS order) to a row dict, renaming
    the DB column `value` to the surface key `data` and decoding JSONB.
    """
    item = dict(zip(_ROW_COLS, row))
    raw = item.pop('value', None)
    if raw is None:
        item['data'] = {}
    elif isinstance(raw, dict):
        item['data'] = raw
    else:
        try:
            item['data'] = json.loads(raw)
        except Exception:
            item['data'] = {}
    return item


class Collection:
    """Fluent interface for a named collection within factory_data."""

    def __init__(self, name: str):
        _validate_collection(name)
        self._name = name

    # ------------------------------------------------------------------ writes

    def set(
        self,
        key: str,
        state: Optional[str] = None,
        data: Optional[dict] = None,
        embedding: Optional[list] = None,
    ) -> str:
        """
        Upsert a row at this key. At least one of `state` or `data` is required.

        - state:     row's state (fires NOTIFY when row is inserted or state changes).
        - data:      JSONB payload.
        - embedding: optional vector, routed to factory_vectors / dim column.

        On INSERT, omitted state defaults to 'new', omitted data defaults to {}.
        On UPDATE, only the fields you pass are changed; the rest are preserved.

        Returns the key (unchanged).
        """
        if state is None and data is None and embedding is None:
            raise ValueError(
                "collection.set requires at least one of state=, data=, embedding="
            )
        if state is not None:
            _validate_state(state)

        try:
            cursor, factory_name = _get_connection()
            insert_state = state or 'new'
            _check_channel_length(factory_name, self._name, insert_state)

            cursor.execute(
                """INSERT INTO factory_data (factory_name, collection, key, user_id, value, state)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                   ON CONFLICT (factory_name, collection, key) DO UPDATE SET
                       value = CASE WHEN %s::boolean THEN EXCLUDED.value ELSE factory_data.value END,
                       state = CASE WHEN %s::boolean THEN EXCLUDED.state ELSE factory_data.state END""",
                (
                    factory_name, self._name, key, 'system',
                    json.dumps(data if data is not None else {}),
                    insert_state,
                    data is not None,
                    state is not None,
                ),
            )
            if embedding is not None:
                self._upsert_embedding(cursor, factory_name, key, embedding)
            return key
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.set failed: {e}")
            raise

    def add(
        self,
        state: str,
        data: Optional[dict] = None,
        embedding: Optional[list] = None,
    ) -> str:
        """
        Insert a new row with an auto-generated UUID key.

        Returns the new key. State is required (every row carries one).
        """
        _validate_state(state)
        key = uuid.uuid4().hex
        try:
            cursor, factory_name = _get_connection()
            _check_channel_length(factory_name, self._name, state)
            cursor.execute(
                """INSERT INTO factory_data (factory_name, collection, key, user_id, value, state)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (factory_name, self._name, key, 'system', json.dumps(data or {}), state),
            )
            if embedding is not None:
                self._upsert_embedding(cursor, factory_name, key, embedding)
            return key
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.add failed: {e}")
            raise

    def remove(self, key: str):
        """Delete one row by key. Cascades to factory_vectors."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "DELETE FROM factory_data "
                "WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._name, key),
            )
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.remove failed: {e}")
            raise

    def _upsert_embedding(self, cursor, factory_name: str, key: str, embedding: list):
        col = _dim_column(embedding)
        cursor.execute(
            f"""INSERT INTO factory_vectors (factory_name, collection, key, {col})
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (factory_name, collection, key)
                DO UPDATE SET {col} = EXCLUDED.{col}""",
            (factory_name, self._name, key, str(embedding)),
        )

    # ------------------------------------------------------------------- reads

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return the full row dict or None."""
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                f"SELECT {', '.join(_ROW_COLS)} FROM factory_data "
                f"WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._name, key),
            )
            row = cursor.fetchone()
            return _row_to_dict(row) if row else None
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.get failed: {e}")
            return None

    def get_all(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all rows in this collection, optionally filtered by state."""
        try:
            cursor, factory_name = _get_connection()
            if state is not None:
                _validate_state(state)
                cursor.execute(
                    f"SELECT {', '.join(_ROW_COLS)} FROM factory_data "
                    f"WHERE factory_name = %s AND collection = %s AND state = %s "
                    f"ORDER BY updated_at DESC",
                    (factory_name, self._name, state),
                )
            else:
                cursor.execute(
                    f"SELECT {', '.join(_ROW_COLS)} FROM factory_data "
                    f"WHERE factory_name = %s AND collection = %s "
                    f"ORDER BY updated_at DESC",
                    (factory_name, self._name),
                )
            return [_row_to_dict(r) for r in cursor.fetchall()]
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.get_all failed: {e}")
            return []

    def count(self, state: Optional[str] = None) -> int:
        """Count rows, optionally filtered by state."""
        try:
            cursor, factory_name = _get_connection()
            if state is not None:
                _validate_state(state)
                cursor.execute(
                    "SELECT COUNT(*) FROM factory_data "
                    "WHERE factory_name = %s AND collection = %s AND state = %s",
                    (factory_name, self._name, state),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM factory_data "
                    "WHERE factory_name = %s AND collection = %s",
                    (factory_name, self._name),
                )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.count failed: {e}")
            return 0

    def exists(self, key: str) -> bool:
        try:
            cursor, factory_name = _get_connection()
            cursor.execute(
                "SELECT 1 FROM factory_data "
                "WHERE factory_name = %s AND collection = %s AND key = %s",
                (factory_name, self._name, key),
            )
            return cursor.fetchone() is not None
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.exists failed: {e}")
            return False

    # ------------------------------------------------------------------ search

    def vector_search(
        self,
        text_or_vec: Union[str, List[float]],
        limit: int = 5,
        state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Vector ANN search. Accepts a query string (auto-embedded via tf.embed)
        or a pre-computed vector. Optionally filter by state.

        Returns row dicts with an extra `similarity` key (cosine, 0..1).
        """
        if isinstance(text_or_vec, str):
            from .embedding import embed
            embedding = embed(text_or_vec)
        else:
            embedding = list(text_or_vec)

        try:
            cursor, factory_name = _get_connection()
            col = _dim_column(embedding)
            sql = f"""
                SELECT {', '.join('d.' + c for c in _ROW_COLS)},
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
            params: List[Any] = [str(embedding), factory_name, self._name]
            if state is not None:
                _validate_state(state)
                sql += " AND d.state = %s"
                params.append(state)
            sql += f" ORDER BY v.{col} <=> %s::vector LIMIT %s"
            params.extend([str(embedding), limit])
            cursor.execute(sql, params)

            results = []
            for row in cursor.fetchall():
                base = _row_to_dict(row[:-1])
                base['similarity'] = float(row[-1])
                results.append(base)
            return results
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection.vector_search failed: {e}")
            return []


def collection(name: str) -> Collection:
    """Access a named data collection for this factory."""
    return Collection(name)
