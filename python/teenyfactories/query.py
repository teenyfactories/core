"""
Lazy collection query builder.

`tf.collection(name)` returns a `CollectionQuery`. Chainable FILTERS
(`.state()`, `.where()`) return a refined query; TERMINALS (`.get_all()`,
`.run()`, `.count()`, `.first()`, iteration) execute. `.vector_search()` and
`.limit()` configure ANN retrieval and are not themselves terminal.

    tf.collection('chunks').state('vectorised') \\
      .where("token_count >= 400 and document != 'X.pdf'") \\
      .vector_search(q).limit(5).run()

The compiler always wraps the user `.where()` predicate inside a code-built
tenant scope prefix — `factory_name = %s AND collection = %s AND ( <user> )` —
so a filter can never widen scope (see where_parser for the parse/parameterize
boundary). Read failures follow the collection contract: log + return empty,
never raise (a missing/typed-bad row must not abort an agent).
"""

from typing import Any, Dict, List, Optional, Union

from .where_parser import compile_where
from .logging import log_error

__all__ = ["CollectionQuery"]

# `.vector_search()` with no explicit `.limit()` falls back to this rather than
# ANN-sorting the whole collection.
_DEFAULT_VECTOR_LIMIT = 10


class CollectionQuery:
    def __init__(self, name: str):
        self._name = name
        self._states: Optional[List[str]] = None
        self._state_set = False
        self._wheres: List[tuple] = []  # list of (sql_fragment, params)
        self._vector: Optional[list] = None  # query embedding
        self._limit: Optional[int] = None
        self._chunk_mode = False       # False = grouped-by-row; True = per-chunk
        self._vec_key: Optional[str] = None  # single-row filter for vector_search

    # ── builder plumbing ─────────────────────────────────────────────────────
    def _clone(self) -> "CollectionQuery":
        q = CollectionQuery(self._name)
        q._states = list(self._states) if self._states is not None else None
        q._state_set = self._state_set
        q._wheres = list(self._wheres)
        q._vector = self._vector
        q._limit = self._limit
        q._chunk_mode = self._chunk_mode
        q._vec_key = self._vec_key
        return q

    # ── filters (chainable) ──────────────────────────────────────────────────
    def state(self, value: Union[str, List[str]]) -> "CollectionQuery":
        """Filter by lifecycle state. Scalar → `= x`; list → `IN (...)`."""
        if self._state_set:
            raise ValueError("state() called more than once; combine into one .state([...]) call")
        from .collection import _validate_state

        states = [value] if isinstance(value, str) else list(value)
        if not states:
            raise ValueError("state() requires at least one state")
        for s in states:
            _validate_state(s)
        q = self._clone()
        q._states = states
        q._state_set = True
        return q

    def where(self, expr: str) -> "CollectionQuery":
        """Add a payload/column predicate via the `.where()` string DSL.

        Multiple `.where()` calls AND together. Raises QueryFilterError on a
        malformed filter (an author bug — surfaced, not swallowed)."""
        sql, params = compile_where(expr)
        q = self._clone()
        q._wheres = self._wheres + [(sql, params)]
        return q

    def vector_search(self, text_or_vec: Union[str, List[float]]) -> "CollectionQuery":
        """Order results by ANN similarity to a query (str auto-embeds)."""
        if isinstance(text_or_vec, str):
            from .embedding import embed

            emb = embed(text_or_vec)
        else:
            emb = list(text_or_vec)
        q = self._clone()
        q._vector = emb
        if q._limit is None:
            q._limit = _DEFAULT_VECTOR_LIMIT
        return q

    def chunks(self) -> "CollectionQuery":
        """Per-CHUNK vector results (no grouping): each returned row is one
        matched chunk-vector. Use for within-a-document search. Default (without
        `.chunks()`) is grouped-by-row — one best chunk per parent row."""
        q = self._clone()
        q._chunk_mode = True
        return q

    def key(self, k: str) -> "CollectionQuery":
        """Restrict a `vector_search` to the vectors owned by a single row
        (a true single-row filter, replacing wide-pool + client-side trim)."""
        q = self._clone()
        q._vec_key = k
        return q

    def limit(self, n: int) -> "CollectionQuery":
        q = self._clone()
        q._limit = int(n)
        return q

    # ── terminals ────────────────────────────────────────────────────────────
    def get_all(self) -> List[Dict[str, Any]]:
        return self._execute()

    def run(self) -> List[Dict[str, Any]]:
        return self._execute()

    def first(self) -> Optional[Dict[str, Any]]:
        rows = self.limit(1)._execute()
        return rows[0] if rows else None

    def count(self) -> int:
        return self._execute_count()

    def __iter__(self):
        return iter(self._execute())

    # ── SQL build + execute ──────────────────────────────────────────────────
    def _scope_and_filters(self, alias: str, params: list) -> str:
        """Emit the tenant scope + state + user predicates, appending params.
        `alias` is the factory_data alias (d). factory_name handled by caller."""
        clauses = []
        if self._states is not None:
            clauses.append(f"{alias}.state = ANY(%s)")
            params.append(self._states)
        for sql, pvals in self._wheres:
            clauses.append(f"({sql})")
            params.extend(pvals)
        return (" AND " + " AND ".join(clauses)) if clauses else ""

    def _execute(self) -> List[Dict[str, Any]]:
        from .collection import _get_connection, _row_to_dict, _ROW_COLS, _dim_column
        from . import db

        try:
            cursor, factory_name = _get_connection()
            params: list = []
            if self._vector is not None:
                col = _dim_column(self._vector)
                qv = str(self._vector)
                pcols = ", ".join("d." + c for c in _ROW_COLS)

                sim = f"1 - (v.{col} <=> %s::vector) AS similarity"
                dist = f"v.{col} <=> %s::vector"

                # Shared FROM/JOIN/WHERE, built once with its params in order.
                # Object-level filters (.state()/.where()) compile against
                # factory_data (alias d); a chunk-meta predicate (v.meta) would
                # slot in here in future without reshaping the join.
                body_params: list = [factory_name, self._name]
                body = (
                    f"  FROM factory_vectors v\n"
                    f"  JOIN factory_data d ON d.factory_name = v.factory_name\n"
                    f"   AND d.collection = v.collection AND d.key = v.key\n"
                    f" WHERE v.factory_name = %s AND v.collection = %s\n"
                    f"   AND v.{col} IS NOT NULL"
                )
                if self._vec_key is not None:
                    body += "\n   AND v.key = %s"
                    body_params.append(self._vec_key)
                body += self._scope_and_filters("d", body_params)

                if self._chunk_mode:
                    # per-chunk: rank chunk-vectors directly.
                    sql = f"SELECT {pcols}, v.chunk_index, v.content, v.meta,\n       {sim}\n" + body
                    sql += f"\n ORDER BY {dist}"
                else:
                    # grouped-by-row: best chunk per parent key (DISTINCT ON key,
                    # nearest chunk), then re-sort the winners by similarity.
                    sql = (
                        "SELECT * FROM (\n"
                        f"SELECT DISTINCT ON (v.key) {pcols}, v.chunk_index, v.content, v.meta,\n"
                        f"       {sim}\n" + body +
                        f"\n ORDER BY v.key, {dist}\n) sub ORDER BY similarity DESC"
                    )
                # qv brackets the body: once in the SELECT similarity, once in
                # the ORDER BY distance.
                params = [qv] + body_params + [qv]
                if self._limit is not None:
                    sql += " LIMIT %s"
                    params.append(self._limit)

                cursor.execute(sql, params)
                n = len(_ROW_COLS)
                out = []
                for row in cursor.fetchall():
                    base = _row_to_dict(row[:n])
                    meta = row[n + 2]
                    if meta is None:
                        meta = {}
                    elif not isinstance(meta, dict):
                        import json as _json
                        try:
                            meta = _json.loads(meta)
                        except Exception:
                            meta = {}
                    base["chunk"] = {"content": row[n + 1], "index": row[n], "meta": meta}
                    base["similarity"] = float(row[n + 3])
                    out.append(base)
                return out

            cols = ", ".join(_ROW_COLS)
            sql = f"SELECT {cols} FROM factory_data d WHERE d.factory_name = %s AND d.collection = %s"
            params.extend([factory_name, self._name])
            sql += self._scope_and_filters("d", params)
            sql += " ORDER BY d.updated_at DESC"
            if self._limit is not None:
                sql += " LIMIT %s"
                params.append(self._limit)
            cursor.execute(sql, params)
            return [_row_to_dict(row) for row in cursor.fetchall()]
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection query failed: {e}")
            return []

    def _execute_count(self) -> int:
        from .collection import _get_connection
        from . import db

        try:
            cursor, factory_name = _get_connection()
            params: list = [factory_name, self._name]
            sql = "SELECT COUNT(*) FROM factory_data d WHERE d.factory_name = %s AND d.collection = %s"
            sql += self._scope_and_filters("d", params)
            cursor.execute(sql, params)
            return int(cursor.fetchone()[0])
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"collection count failed: {e}")
            return 0
