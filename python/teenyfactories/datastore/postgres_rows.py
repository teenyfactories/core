"""PostgreSQL row and atomic vector operations."""
import json

from .. import config, db

ROW_COLS = ['factory_name', 'collection', 'key', 'user_id', 'value', 'state', 'created_at', 'updated_at']
SUPPORTED_DIMS = (256, 512, 768, 1024, 1536, 3072)


def dim_column(vector):
    if len(vector) not in SUPPORTED_DIMS:
        raise ValueError(f"Unsupported embedding dim {len(vector)}; must be one of {SUPPORTED_DIMS}")
    return f"embedding_{len(vector)}"


def row_to_dict(row):
    item = dict(zip(ROW_COLS, row))
    raw = item.pop('value', None)
    if isinstance(raw, dict):
        item['data'] = raw
    else:
        try:
            item['data'] = json.loads(raw) if raw is not None else {}
        except Exception:
            item['data'] = {}
    return item


class PostgresRows:
    def upsert(self, collection, key, state, data):
        with db.transaction() as cur:
            cur.execute(
                """INSERT INTO factory_data (factory_name, collection, key, user_id, value, state)
                   VALUES (%s, %s, %s, 'system', %s::jsonb, %s)
                   ON CONFLICT (factory_name, collection, key) DO UPDATE SET
                     value = CASE WHEN %s::boolean THEN EXCLUDED.value ELSE factory_data.value END,
                     state = CASE WHEN %s::boolean THEN EXCLUDED.state ELSE factory_data.state END""",
                (config.FACTORY_NAME, collection, key, json.dumps(data if data is not None else {}),
                 state or 'new', data is not None, state is not None),
            )
        return key

    def insert(self, collection, key, state, data):
        with db.transaction() as cur:
            cur.execute(
                """INSERT INTO factory_data (factory_name, collection, key, user_id, value, state)
                   VALUES (%s, %s, %s, 'system', %s::jsonb, %s)""",
                (config.FACTORY_NAME, collection, key, json.dumps(data or {}), state),
            )
        return key

    def remove(self, collection, key):
        with db.transaction() as cur:
            cur.execute("DELETE FROM factory_data WHERE factory_name=%s AND collection=%s AND key=%s",
                        (config.FACTORY_NAME, collection, key))

    def get(self, collection, key):
        with db.transaction() as cur:
            cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM factory_data "
                        "WHERE factory_name=%s AND collection=%s AND key=%s",
                        (config.FACTORY_NAME, collection, key))
            row = cur.fetchone()
        return row_to_dict(row) if row else None

    def exists(self, collection, key):
        with db.transaction() as cur:
            cur.execute("SELECT 1 FROM factory_data WHERE factory_name=%s AND collection=%s AND key=%s",
                        (config.FACTORY_NAME, collection, key))
            return cur.fetchone() is not None

    def replace_vectors(self, collection, key, items):
        column = None
        for item in items:
            candidate = dim_column(item['embedding'])
            if column is not None and candidate != column:
                raise ValueError("set_vectors: all items in one call must share an embedding dimension")
            column = candidate
        with db.transaction() as cur:
            cur.execute("DELETE FROM factory_vectors WHERE factory_name=%s AND collection=%s AND key=%s",
                        (config.FACTORY_NAME, collection, key))
            if items:
                cur.executemany(
                    f"""INSERT INTO factory_vectors
                       (factory_name, collection, key, chunk_index, content, meta, {column})
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::vector)""",
                    [(config.FACTORY_NAME, collection, key, idx, item['content'],
                      json.dumps(item['meta']), str(item['embedding'])) for idx, item in enumerate(items)],
                )
        return len(items)
