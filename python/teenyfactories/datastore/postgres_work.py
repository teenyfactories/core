"""Work lookup and atomic claims; durable state stays authoritative."""
import json

from .. import config, db
from .postgres_rows import row_to_dict


class PostgresWork:
    def peek_next(self, subs, bound, exclude=None):
        if not subs:
            return None
        values = ','.join(['(%s,%s,%s,%s,%s)'] * len(subs))
        params = []
        for coll, state, tier, priority, delay in subs:
            params.extend([coll, state, int(tier), int(priority), float(delay)])
        params.extend([config.FACTORY_NAME, int(bound)])
        exclusions = ''
        if exclude:
            placeholders = ','.join(['(%s,%s,%s,%s::timestamptz)'] * len(exclude))
            exclusions = f'AND (d.collection,d.key,d.state,d.state_changed_at) NOT IN (VALUES {placeholders})'
            for item in exclude:
                params.extend(item)
        sql = f"""
            WITH subs(collection,state,tier,priority,delay) AS (VALUES {values})
            SELECT d.factory_name,d.collection,d.key,d.user_id,d.value,d.state,
                   d.created_at,d.updated_at,d.state_changed_at
              FROM factory_data d
              JOIN subs s ON s.collection=d.collection AND s.state=d.state
             WHERE d.factory_name=%s
               AND d.state_changed_at+(s.delay::double precision*INTERVAL '1 second')<=NOW()
               AND (s.tier::int=0 OR s.priority::int<%s)
               AND NOT EXISTS (
                   SELECT 1 FROM public.factory_job_claims c
                    WHERE c.factory_name=d.factory_name AND c.lease_expires_at>NOW()
                      AND c.claim_data->>'collection'=d.collection
                      AND c.claim_data->>'key'=d.key
                      AND c.claim_data->>'source_state'=d.state)
               {exclusions}
             ORDER BY s.tier::int,s.priority::int,d.state_changed_at,d.key LIMIT 1
        """
        with db.transaction() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return {**row_to_dict(row[:8]), 'state_changed_at': row[8]} if row else None

    def try_claim(self, collection, key, state, state_changed_at, ttl_seconds):
        from ..claims import hash_claim_key, _normalize_timestamp, _worker_id
        claim_key = hash_claim_key(collection, key, state, state_changed_at)
        data = {'collection': collection, 'key': key, 'source_state': state,
                'source_state_changed_at': _normalize_timestamp(state_changed_at), 'claimed_by': _worker_id()}
        with db.transaction() as cur:
            cur.execute(
                """WITH candidate AS (
                     SELECT 1 FROM public.factory_data
                      WHERE factory_name=%s AND collection=%s AND key=%s AND state=%s AND state_changed_at=%s
                      FOR UPDATE SKIP LOCKED)
                   INSERT INTO public.factory_job_claims (factory_name,claim_key,claim_data,lease_expires_at)
                   SELECT %s,%s,%s::jsonb,NOW()+(%s*INTERVAL '1 second') FROM candidate
                   ON CONFLICT (factory_name,claim_key) DO NOTHING RETURNING claim_key""",
                (config.FACTORY_NAME, collection, key, state, state_changed_at,
                 config.FACTORY_NAME, claim_key, json.dumps(data), float(ttl_seconds)),
            )
            return cur.fetchone() is not None

    def release_claim(self, collection, key, state, state_changed_at):
        from ..claims import hash_claim_key, _worker_id
        with db.transaction() as cur:
            cur.execute(
                """DELETE FROM public.factory_job_claims
                    WHERE factory_name=%s AND claim_key=%s AND claim_data->>'claimed_by'=%s""",
                (config.FACTORY_NAME, hash_claim_key(collection, key, state, state_changed_at), _worker_id()),
            )

    def reap_claims(self):
        with db.transaction() as cur:
            cur.execute("DELETE FROM public.factory_job_claims WHERE factory_name=%s AND lease_expires_at<NOW() "
                        "RETURNING claim_data->>'collection'", (config.FACTORY_NAME,))
            rows = cur.fetchall()
            if rows:
                cur.execute("SELECT pg_notify('tf_data_changed', %s)", (json.dumps({'factory_name': config.FACTORY_NAME}),))
        return [row[0] for row in rows]
