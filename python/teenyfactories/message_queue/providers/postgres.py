"""PostgreSQL connection + LISTEN/NOTIFY primitives for the message queue.

Messages and lifecycle items both live in factory_data now. This provider
just maintains a connection with autocommit, runs LISTEN on channels that
subscribers register, and surfaces raw notifications. All topic logic is in
``message_queue.base``.
"""

import json
from typing import List, Optional

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

from teenyfactories import config
from teenyfactories.logging import log_info, log_error


class PostgresProvider:
    """Thin connection + LISTEN wrapper."""

    def __init__(self):
        self.connection = None
        self.cursor = None                # Used for reads AND direct writes to factory_data
        self._factory_name = config.FACTORY_NAME
        self._agent_name = config.AGENT_NAME
        self._listening = set()           # set of channel names we've LISTENed on

    def connect(self):
        if psycopg2 is None:
            raise ImportError("psycopg2 not available — install with 'pip install psycopg2-binary'")

        conn_args = dict(
            host=config.POSTGRES_HOST,
            port=config.POSTGRES_PORT,
            database=config.POSTGRES_DB,
            user=config.POSTGRES_USER,
            password=config.POSTGRES_PASSWORD,
        )
        self.connection = psycopg2.connect(**conn_args)
        self.connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        self.cursor = self.connection.cursor()

        log_info(f"Connected to PostgreSQL at {config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}")

    # =========================================================================
    # LISTEN / NOTIFY
    # =========================================================================

    def listen(self, channel: str):
        """Issue LISTEN on a channel if we haven't already."""
        if channel in self._listening:
            return
        # Quote identifier for Postgres — channel may contain dots/underscores.
        self.cursor.execute(f'LISTEN "{channel}"')
        self._listening.add(channel)
        log_info(f"LISTEN on channel: {channel}")

    def poll_notifications(self) -> List[dict]:
        """Drain queued NOTIFYs. Returns a list of {channel, payload} dicts."""
        if not self.connection:
            return []
        try:
            self.connection.poll()
        except Exception as e:
            log_error(f"poll() failed: {e}")
            return []

        notifications = []
        while self.connection.notifies:
            notify = self.connection.notifies.pop(0)
            payload = notify.payload
            try:
                if payload:
                    payload = json.loads(payload)
            except Exception:
                pass  # leave as string if not JSON
            notifications.append({'channel': notify.channel, 'payload': payload})
        return notifications

    # =========================================================================
    # Data fetch helper
    # =========================================================================

    def fetch_item(self, factory_name: str, collection: str, key: str) -> Optional[dict]:
        """Fetch a single factory_data row as a full item dict."""
        try:
            self.cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state, created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s AND collection = %s AND key = %s""",
                (factory_name, collection, key)
            )
            row = self.cursor.fetchone()
            if not row:
                return None
            raw = row[4]
            if raw is None:
                payload = {}
            elif isinstance(raw, dict):
                payload = raw
            else:
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {}
            return {
                'factory_name': row[0],
                'collection':   row[1],
                'key':          row[2],
                'user_id':      row[3],
                'data':         payload,
                'state':        row[5],
                'created_at':   row[6],
                'updated_at':   row[7],
            }
        except Exception as e:
            log_error(f"fetch_item failed: {e}")
            return None
