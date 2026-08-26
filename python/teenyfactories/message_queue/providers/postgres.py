"""PostgreSQL NOTIFY-wake + poll primitives for the message queue.

Everything lives in factory_data. This provider rides the process-wide shared
connection (``teenyfactories.db``), LISTENs the single global
``tf_data_changed`` wake channel, and exposes ``peek_next`` — the single best
ready+unclaimed row across all subscriptions, ordered by (tier, priority,
state_changed_at, key), in one query. All dispatch/strike logic is in
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
from typing import List, Optional

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
        "factory_name": row[0],
        "collection": row[1],
        "key": row[2],
        "user_id": row[3],
        "data": payload,
        "state": row[5],
        "created_at": row[6],
        "updated_at": row[7],
        "state_changed_at": row[8],
    }


class PostgresProvider:
    """LISTEN owner + poll scans on the shared connection."""

    def __init__(self):
        self._factory_name = config.FACTORY_NAME
        self._agent_name = config.AGENT_NAME
        self._listening = set()  # channels we WANT listened
        self._listen_generation = -1  # db.generation() we last LISTENed on

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
            self._listen_generation = -1  # force re-issue including the new channel
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
            notifications.append({"channel": notify.channel, "payload": payload})
        return notifications

    # =========================================================================
    # Preemptive finder — the single best READY, UNCLAIMED row across ALL
    # subscribed (collection, state), in one query. Used by the tiered poll loop.
    # (The old per-state batch scans `fetch_rows` / `fetch_due_rows` were retired
    # when dispatch became preemptive: a static batch can't let a higher-priority
    # row that arrives mid-drain jump the queue — see peek_next below.)
    # =========================================================================

    def peek_next(self, subs: List[tuple], bound: int) -> Optional[dict]:
        """The best ready, unclaimed row across all subscriptions, or None.

        `subs`: list of (collection, state, tier, priority, delay) from
        base._subscription_rows(). Ordering is (tier, priority, state_changed_at,
        key): tier 0 (MCP `_mcp_*` requests) is ALWAYS eligible and sorts first;
        tier 1 (ordinary state) is eligible only when its priority < `bound` —
        `bound` is the best due scheduled job's `.priority()`, so a state row is
        returned ONLY when it beats the scheduled job that would otherwise run.
        Ready = the per-subscription delay has elapsed. Unclaimed = no LIVE claim
        on the row (anti-join on public.factory_job_claims by the claim_data
        tuple claims.py stamps; expired leases don't hide a row).

        # GOAL: check + claim in ONE db call. This is the CHECK half only — a
        # read-only peek; the caller then CLAIMS the winner via the proven
        # claims.try_claim (so find+claim is currently TWO db calls). Fusing the
        # claim INTO this statement (SQL-computed claim_key + delete-by-tuple
        # release) is Stage 2 of [tf:priority-not-preemptive-mid-drain] — it is
        # gated on a PG dual-test + db/security review (rolling-deploy claim-key
        # coexistence is the load-bearing risk), so it is deliberately NOT done
        # here. The anti-join keeps this livelock-free: a row another worker
        # already holds is skipped, so a claim race just re-peeks past it.

        LIMIT 1 with a per-unit re-query is inherent to PREEMPTION (a static
        batch can't let a higher-priority row arriving mid-drain jump the queue);
        the old batched `fetch_rows` drain is retired for exactly that reason.
        """
        if not subs:
            return None
        values_sql = ",".join(["(%s,%s,%s,%s,%s)"] * len(subs))
        params: list = []
        for coll, state, tier, pri, dly in subs:
            params.extend([coll, state, int(tier), int(pri), float(dly)])
        params.append(self._factory_name)
        params.append(int(bound))
        # Casts (::int / ::double precision) are required: an all-placeholder
        # VALUES gives every subs column `unknown` type, so arithmetic and the
        # bound comparison would fail without them.
        sql = f"""
            WITH subs(collection, state, tier, priority, delay) AS (
                VALUES {values_sql}
            ),
            candidate AS (
                SELECT d.factory_name, d.collection, d.key, d.user_id, d.value,
                       d.state, d.created_at, d.updated_at, d.state_changed_at
                FROM factory_data d
                JOIN subs s ON s.collection = d.collection AND s.state = d.state
                WHERE d.factory_name = %s
                  AND d.state_changed_at + (s.delay::double precision * INTERVAL '1 second') <= NOW()
                  AND (s.tier::int = 0 OR s.priority::int < %s)
                  AND NOT EXISTS (
                      SELECT 1 FROM public.factory_job_claims fjc
                      WHERE fjc.factory_name = d.factory_name
                        AND fjc.lease_expires_at > NOW()
                        AND fjc.claim_data->>'collection'   = d.collection
                        AND fjc.claim_data->>'key'          = d.key
                        AND fjc.claim_data->>'source_state' = d.state
                  )
                ORDER BY s.tier::int, s.priority::int, d.state_changed_at, d.key
                LIMIT 1
            )
            SELECT factory_name, collection, key, user_id, value, state,
                   created_at, updated_at, state_changed_at
            FROM candidate
        """
        try:
            with db.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return _row_to_item(row) if row else None
        except Exception as e:
            db.invalidate_if_dead(e)
            log_error(f"peek_next failed: {e}")
            return None
