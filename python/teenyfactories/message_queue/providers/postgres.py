"""PostgreSQL NOTIFY-wake + poll primitives for the message queue.

Everything lives in factory_data. This provider rides the process-wide shared
connection (``teenyfactories.db``), LISTENs the single global
``tf_data_changed`` wake channel, and exposes plain ``ORDER BY (updated_at,
key)`` scans of a ``(collection, state)``. All dispatch/strike logic is in
``message_queue.base``.

This provider is the LISTEN owner: it tracks ``db.generation()`` and
re-issues LISTEN whenever the shared connection was replaced after a
failure. Everything else mints throwaway cursors per call.

There is no per-state channel and no client-side channel hashing anymore.
``tf_data_changed`` is emitted by the DB trigger (migration
``2026-05-09T0536_notify_generic_channels.sql``) on every factory_data write
with a JSON payload that includes ``factory_name``; base.py uses it purely as
an advisory "poll now" wake, filtered by ``factory_name``.
"""

import json
from typing import List

from teenyfactories import config, db
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
        'factory_name':     row[0],
        'collection':       row[1],
        'key':              row[2],
        'user_id':          row[3],
        'data':             payload,
        'state':            row[5],
        'created_at':       row[6],
        'updated_at':       row[7],
        'state_changed_at': row[8],
    }


class PostgresProvider:
    """LISTEN owner + poll scans on the shared connection."""

    def __init__(self):
        self._factory_name = config.FACTORY_NAME
        self._agent_name = config.AGENT_NAME
        self._listening = set()           # channels we WANT listened
        self._listen_generation = -1      # db.generation() we last LISTENed on

    # =========================================================================
    # LISTEN / NOTIFY (wake only)
    # =========================================================================

    def _ensure_listening(self):
        """Re-issue LISTEN for every wanted channel when the shared connection
        was (re)opened since we last LISTENed. Returns the live connection."""
        conn = db.get_connection()
        gen = db.generation()
        if gen != self._listen_generation:
            with conn.cursor() as cur:
                for channel in self._listening:
                    # Quote identifier — channel may contain underscores.
                    cur.execute(f'LISTEN "{channel}"')
            self._listen_generation = gen
        return conn

    def listen(self, channel: str):
        """Register a channel and issue LISTEN. Idempotent."""
        if channel not in self._listening:
            self._listening.add(channel)
            self._listen_generation = -1   # force re-issue including the new channel
            log_debug(f"LISTEN on channel: {channel}")
        self._ensure_listening()

    def poll_notifications(self) -> List[dict]:
        """Drain queued NOTIFYs. Returns a list of {channel, payload} dicts.

        base.py only inspects payload['factory_name'] (advisory wake).
        """
        try:
            conn = self._ensure_listening()
            conn.poll()
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"poll() failed: {e}")
            return []

        notifications = []
        while conn.notifies:
            notify = conn.notifies.pop(0)
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

        FIFO = ORDER BY state_changed_at ASC, key ASC (the order rows entered
        this state — the previous `updated_at` ordering would re-shuffle a
        row whose value got updated without a state change, contradicting
        the queue-arrival intuition). No cursor: the state itself is the
        queue — a row still here is still pending; the handler removes it
        by transitioning/deleting.
        """
        try:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT factory_name, collection, key, user_id, value, state,
                              created_at, updated_at, state_changed_at
                       FROM factory_data
                       WHERE factory_name = %s AND collection = %s AND state = %s
                       ORDER BY state_changed_at ASC, key ASC""",
                    (self._factory_name, collection, state),
                )
                return [_row_to_item(r) for r in cur.fetchall()]
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"fetch_rows failed for {collection}.{state}: {e}")
            return []

    def fetch_due_rows(
        self,
        collection: str,
        state: str,
        delay_seconds: float,
    ) -> List[dict]:
        """Rows in (collection, state) whose delay has elapsed.

        Adds `state_changed_at + delay_seconds <= NOW()` to fetch_rows. The
        delay clock measures time-since-state-entry, NOT time-since-last-
        touch — so an in-flight value update on a same-state row doesn't
        reset the delay. The delay is a pure predicate — no cursor. Strict
        cancellation: if the row left the state it simply isn't returned.
        """
        try:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT factory_name, collection, key, user_id, value, state,
                              created_at, updated_at, state_changed_at
                       FROM factory_data
                       WHERE factory_name = %s
                         AND collection   = %s
                         AND state        = %s
                         AND state_changed_at + (%s * INTERVAL '1 second') <= NOW()
                       ORDER BY state_changed_at ASC, key ASC""",
                    (self._factory_name, collection, state, float(delay_seconds)),
                )
                return [_row_to_item(r) for r in cur.fetchall()]
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"fetch_due_rows failed for {collection}.{state}: {e}")
            return []
