"""PostgreSQL connection + NOTIFY-wake + poll primitives for the message queue.

Everything lives in factory_data. This provider maintains an autocommit
connection, LISTENs the single global ``tf_data_changed`` wake channel, and
exposes plain ``ORDER BY (updated_at, key)`` scans of a ``(collection,
state)``. All dispatch/strike logic is in ``message_queue.base``.

There is no per-state channel and no client-side channel hashing anymore.
``tf_data_changed`` is emitted by the DB trigger (migration
``2026-05-09T0536_notify_generic_channels.sql``) on every factory_data write
with a JSON payload that includes ``factory_name``; base.py uses it purely as
an advisory "poll now" wake, filtered by ``factory_name``.
"""

import json
from typing import List

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

from teenyfactories import config
from teenyfactories.logging import log_debug, log_error


# Single global wake channel. Emitted by the factory_data NOTIFY trigger
# (migration 2026-05-09T0536_notify_generic_channels.sql) on every write;
# payload is JSON including factory_name. base.py LISTENs only this and
# treats any own-factory fire as "poll now".
TF_DATA_CHANGED_CHANNEL = "tf_data_changed"


def _row_to_item(row) -> dict:
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


class PostgresProvider:
    """Thin connection + LISTEN wrapper."""

    def __init__(self):
        self.connection = None
        self.cursor = None                # reads AND direct writes to factory_data
        self._factory_name = config.FACTORY_NAME
        self._agent_name = config.AGENT_NAME
        self._listening = set()           # channels we've LISTENed on

    def connect(self):
        if psycopg2 is None:
            raise ImportError("psycopg2 not available — install with 'pip install psycopg2-binary'")

        # config.connect_postgres() handles psycopg2.connect + isolation level
        # + RLS scope SET (app.factory_name = FACTORY_NAME). Single source of
        # truth — every tf-core connect routes through here.
        self.connection = config.connect_postgres()
        self.cursor = self.connection.cursor()

        log_debug(f"Connected to PostgreSQL at {config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}")

    # =========================================================================
    # LISTEN / NOTIFY (wake only)
    # =========================================================================

    def listen(self, channel: str):
        """Issue LISTEN on a channel if we haven't already. Idempotent."""
        if channel in self._listening:
            return
        # Quote identifier — channel may contain underscores.
        self.cursor.execute(f'LISTEN "{channel}"')
        self._listening.add(channel)
        log_debug(f"LISTEN on channel: {channel}")

    def poll_notifications(self) -> List[dict]:
        """Drain queued NOTIFYs. Returns a list of {channel, payload} dicts.

        base.py only inspects payload['factory_name'] (advisory wake).
        """
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
    # Poll scans — plain (updated_at, key) FIFO, no cursor
    # =========================================================================

    def fetch_rows(self, collection: str, state: str) -> List[dict]:
        """Every row currently in (collection, state), oldest first.

        FIFO = ORDER BY updated_at ASC, key ASC (the order rows entered the
        state). No cursor: the state itself is the queue — a row still here
        is still pending; the handler removes it by transitioning/deleting.
        """
        try:
            self.cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state,
                          created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s AND collection = %s AND state = %s
                   ORDER BY updated_at ASC, key ASC""",
                (self._factory_name, collection, state),
            )
            return [_row_to_item(r) for r in self.cursor.fetchall()]
        except Exception as e:
            log_error(f"fetch_rows failed for {collection}.{state}: {e}")
            return []

    def fetch_due_rows(
        self,
        collection: str,
        state: str,
        delay_seconds: float,
    ) -> List[dict]:
        """Rows in (collection, state) whose delay has elapsed.

        Adds `updated_at + delay_seconds <= NOW()` to fetch_rows. The delay
        is a pure predicate — no cursor. Strict cancellation: if the row
        left the state it simply isn't returned.
        """
        try:
            self.cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state,
                          created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s
                     AND collection   = %s
                     AND state        = %s
                     AND updated_at + (%s * INTERVAL '1 second') <= NOW()
                   ORDER BY updated_at ASC, key ASC""",
                (self._factory_name, collection, state, float(delay_seconds)),
            )
            return [_row_to_item(r) for r in self.cursor.fetchall()]
        except Exception as e:
            log_error(f"fetch_due_rows failed for {collection}.{state}: {e}")
            return []
