"""Compile a neutral collection-query request into PostgreSQL SQL."""
import json

from .. import config, db
from .postgres_filters import compile_filter
from .postgres_rows import ROW_COLS, dim_column, row_to_dict


def _filters(query, params):
    clauses = []
    if query._states is not None:
        clauses.append("d.state = ANY(%s)")
        params.append(query._states)
    for node in query._wheres:
        sql, values = compile_filter(node)
        clauses.append(f"({sql})")
        params.extend(values)
    return (" AND " + " AND ".join(clauses)) if clauses else ""


class PostgresQueries:
    def query(self, query):
        params = [config.FACTORY_NAME, query._name]
        if query._vector is None:
            sql = f"SELECT {', '.join(ROW_COLS)} FROM factory_data d WHERE d.factory_name=%s AND d.collection=%s"
            sql += _filters(query, params) + " ORDER BY d.updated_at DESC"
            if query._limit is not None:
                sql += " LIMIT %s"
                params.append(query._limit)
            with db.transaction() as cur:
                cur.execute(sql, params)
                return [row_to_dict(row) for row in cur.fetchall()]

        column = dim_column(query._vector)
        vector = str(query._vector)
        body = (" FROM factory_vectors v JOIN factory_data d ON d.factory_name=v.factory_name "
                "AND d.collection=v.collection AND d.key=v.key WHERE v.factory_name=%s "
                f"AND v.collection=%s AND v.{column} IS NOT NULL")
        if query._vec_key is not None:
            body += " AND v.key=%s"
            params.append(query._vec_key)
        body += _filters(query, params)
        cols = ', '.join('d.' + column_name for column_name in ROW_COLS)
        fields = f"{cols}, v.chunk_index, v.content, v.meta, 1-(v.{column} <=> %s::vector) AS similarity"
        if query._chunk_mode:
            sql = f"SELECT {fields}{body} ORDER BY v.{column} <=> %s::vector"
        else:
            sql = (f"SELECT * FROM (SELECT DISTINCT ON (v.key) {fields}{body} "
                   f"ORDER BY v.key, v.{column} <=> %s::vector) sub ORDER BY similarity DESC")
        params = [vector] + params + [vector]
        if query._limit is not None:
            sql += " LIMIT %s"
            params.append(query._limit)
        with db.transaction() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        result = []
        for row in rows:
            item = row_to_dict(row[:len(ROW_COLS)])
            meta = row[len(ROW_COLS) + 2]
            if not isinstance(meta, dict):
                try:
                    meta = json.loads(meta) if meta is not None else {}
                except Exception:
                    meta = {}
            item['chunk'] = {'index': row[len(ROW_COLS)], 'content': row[len(ROW_COLS) + 1], 'meta': meta}
            item['similarity'] = float(row[len(ROW_COLS) + 3])
            result.append(item)
        return result

    def count(self, query):
        params = [config.FACTORY_NAME, query._name]
        sql = "SELECT COUNT(*) FROM factory_data d WHERE d.factory_name=%s AND d.collection=%s"
        sql += _filters(query, params)
        with db.transaction() as cur:
            cur.execute(sql, params)
            return int(cur.fetchone()[0])
