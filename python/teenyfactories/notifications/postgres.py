"""Direct PostgreSQL wakes; never select this provider for transaction-pooled SQL."""
import json

from .. import config, db


class PostgresNotifications:
    def __init__(self):
        self._generation = -1

    def _listen(self, conn):
        if db.generation() != self._generation:
            with conn.cursor() as cur:
                cur.execute('LISTEN tf_data_changed')
            self._generation = db.generation()

    def update_subscriptions(self, subscriptions):
        with db.session() as conn:
            self._listen(conn)

    def consume_wake(self):
        try:
            with db.session() as conn:
                self._listen(conn)
                conn.poll()
                pending = conn.notifies[:]
                del conn.notifies[:]
            for note in pending:
                try:
                    payload = json.loads(note.payload)
                    if isinstance(payload, dict) and payload.get('factory_name') == config.FACTORY_NAME:
                        return True
                except (TypeError, ValueError):
                    pass
            return False
        except Exception as exc:
            db.invalidate_if_dead(exc)
            raise

    def close(self):
        pass  # SQL and direct notifications deliberately share the direct-mode session.
