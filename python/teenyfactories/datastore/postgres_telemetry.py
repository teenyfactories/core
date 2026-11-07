"""Scoped log, usage and breakpoint persistence."""
import json

from .. import config, db


class PostgresTelemetry:
    def append_log(self, service, container, level, message, data=None):
        with db.transaction() as cur:
            cur.execute(
                """INSERT INTO factory_logs
                   (factory_name, service_name, container_id, user_id, level, message, log_data)
                   VALUES (%s,%s,%s,'system',%s,%s,%s::jsonb) RETURNING id""",
                (config.FACTORY_NAME, service, container, level, message,
                 json.dumps(data) if data is not None else None),
            )
            return cur.fetchone()[0]

    def read_log(self, log_id):
        with db.transaction() as cur:
            cur.execute("SELECT log_data FROM factory_logs WHERE id=%s AND factory_name=%s",
                        (log_id, config.FACTORY_NAME))
            row = cur.fetchone()
            return row[0] if row else None

    def record_usage(self, values):
        with db.transaction() as cur:
            cur.execute("SELECT public.record_ai_usage(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)", values)
            row = cur.fetchone()
            return row[0] if row else None
