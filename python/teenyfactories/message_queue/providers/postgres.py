"""PostgreSQL connection + LISTEN/NOTIFY primitives for the message queue.

Messages and lifecycle items both live in factory_data now. This provider
just maintains a connection with autocommit, runs LISTEN on channels that
subscribers register, and surfaces raw notifications. All topic logic is in
``message_queue.base``.

NOTIFY channel names are hashed (md5) to dodge Postgres's 63-byte
NAMEDATALEN-1 limit on plaintext `{factory}.{collection}.{state}` strings.
The trigger emits on `tf_state_<32hex>` and `tf_collection_<32hex>`; tf core
LISTENs on the same hashed names. Payload-side validation in
`message_queue.base._drain_notifications` confirms the incoming row matches
the handler's registered (collection, state) — collisions are vanishingly
improbable (1 in 3.4×10^38) but the guard logs and drops them either way.
"""

import hashlib
import json
from typing import List, Optional, Tuple

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:
    psycopg2 = None

from teenyfactories import config
from teenyfactories.logging import log_info, log_error


# =============================================================================
# Channel-name hashing — keeps LISTEN/NOTIFY targets under the 63-byte cap
# =============================================================================

def hash_state_channel(factory: str, collection: str, state: str) -> str:
    """Hashed channel for per-(collection, state) NOTIFY. 41 chars."""
    h = hashlib.md5(f"{factory}.{collection}.{state}".encode("utf-8")).hexdigest()
    return f"tf_state_{h}"


def hash_collection_channel(factory: str, collection: str) -> str:
    """Hashed channel for any-state per-collection NOTIFY. 46 chars."""
    h = hashlib.md5(f"{factory}.{collection}".encode("utf-8")).hexdigest()
    return f"tf_collection_{h}"


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

    def fetch_rows_since(
        self,
        collection: str,
        state: str,
        cursor: Optional[Tuple],
    ) -> List[dict]:
        """Rows in (collection, state) strictly after the composite cursor.

        Used by the 10s safety poll to catch anything LISTEN missed. Cursor
        is a `(updated_at, key)` tuple — composite to handle equal-timestamp
        rows correctly (a naive `> updated_at` filter loses rows when two
        writes share the same timestamp).

        Pass `cursor=None` to fetch every row (used by the startup
        replay-all path).
        """
        try:
            base_sql = (
                "SELECT factory_name, collection, key, user_id, value, state, "
                "       created_at, updated_at "
                "FROM factory_data "
                "WHERE factory_name = %s AND collection = %s AND state = %s"
            )
            order_sql = " ORDER BY updated_at ASC, key ASC"

            if cursor is None:
                self.cursor.execute(
                    base_sql + order_sql,
                    (self._factory_name, collection, state),
                )
            else:
                cursor_ts, cursor_key = cursor
                self.cursor.execute(
                    base_sql + " AND (updated_at, key) > (%s, %s)" + order_sql,
                    (self._factory_name, collection, state, cursor_ts, cursor_key),
                )

            rows = self.cursor.fetchall()
            out = []
            for row in rows:
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
                out.append({
                    'factory_name': row[0],
                    'collection':   row[1],
                    'key':          row[2],
                    'user_id':      row[3],
                    'data':         payload,
                    'state':        row[5],
                    'created_at':   row[6],
                    'updated_at':   row[7],
                })
            return out
        except Exception as e:
            log_error(f"fetch_rows_since failed for {collection}.{state}: {e}")
            return []

    def fetch_due_rows(
        self,
        collection: str,
        state: str,
        cursor: Tuple,
        delay_seconds: float,
    ) -> List[dict]:
        """Rows due for delayed dispatch.

        Filters on:
          state = $state                      — strict cancellation
          (updated_at, key) > cursor          — no re-fire (per-handler cursor)
          updated_at + delay_seconds <= NOW() — the delay floor

        Composite cursor + ASC ordering match `fetch_rows_since`.
        """
        try:
            cursor_ts, cursor_key = cursor
            self.cursor.execute(
                """SELECT factory_name, collection, key, user_id, value, state,
                          created_at, updated_at
                   FROM factory_data
                   WHERE factory_name = %s
                     AND collection   = %s
                     AND state        = %s
                     AND (updated_at, key) > (%s, %s)
                     AND updated_at + (%s * INTERVAL '1 second') <= NOW()
                   ORDER BY updated_at ASC, key ASC""",
                (
                    self._factory_name,
                    collection,
                    state,
                    cursor_ts,
                    cursor_key,
                    float(delay_seconds),
                ),
            )
            rows = self.cursor.fetchall()
            out = []
            for row in rows:
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
                out.append({
                    'factory_name': row[0],
                    'collection':   row[1],
                    'key':          row[2],
                    'user_id':      row[3],
                    'data':         payload,
                    'state':        row[5],
                    'created_at':   row[6],
                    'updated_at':   row[7],
                })
            return out
        except Exception as e:
            log_error(f"fetch_due_rows failed for {collection}.{state}: {e}")
            return []

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
