"""
Atomic job claims for handler dispatch.

Wraps every `tf.on_state(...).do(handler)` invocation so that multiple pods
(replicas, orphans from rolling restarts, network-partitioned zombies) can't
process the same (collection, key, state-cycle) twice in parallel.

Mechanism: deterministic SHA-256 claim_key in `public.factory_job_claims`.
Acquisition is a single CTE statement — `SELECT … FROM factory_data WHERE
(coll, key, state, state_changed_at) match FOR UPDATE SKIP LOCKED`, then
`INSERT … FROM that candidate ON CONFLICT DO NOTHING RETURNING claim_key`.
The predicate on the source row's `state_changed_at` closes the stale-
snapshot re-claim race (a late worker that polled the row before worker A
finished + released would otherwise be free to re-claim post-release, since
A's DELETE freed the PK). Release on handler completion DELETEs the claim
row. A janitor sweep (called from `tf.run_pending`) reaps stale claims past
`lease_expires_at` — covering the crashed-mid-handler case where the
`finally` block never ran.

RLS on the table auto-scopes every query to `current_setting('app.factory_name')`
so the global-looking janitor DELETE only sees the caller's own factory.

Public surface used by message_queue dispatcher:
    try_claim(coll, key, state, state_changed_at, ttl_seconds) -> bool
    release_claim(coll, key, state, state_changed_at) -> None
    janitor_sweep() -> int      # rows reaped; called periodically from run_pending
    hash_claim_key(coll, key, state, state_changed_at) -> str    # SHA-256 hex
"""

import hashlib
import os
import time
from datetime import datetime, timezone

from . import config, db
from .logging import log_debug, log_warn


# Janitor cadence: per pod, every ~30 seconds during run_pending.
_JANITOR_INTERVAL_SECONDS = 30.0
_last_janitor_tick: float = 0.0

# Default claim duration if .claim_duration() is not chained. 1 hour. The
# SubscriptionBuilder reads this when constructing a registration so each
# subscription can override via .claim_duration(seconds/minutes/hours).
DEFAULT_CLAIM_DURATION_SECONDS = 3600.0


# ── Worker identity ─────────────────────────────────────────────────────────

def _worker_id() -> str:
    """The claimed_by value stamped on every claim. k8s pod name (HOSTNAME)
    in production; falls back to a process-local string in dev environments
    where HOSTNAME might be unset or non-unique. Uniqueness in dev is a
    known gap (deferred — see roadmap)."""
    return os.environ.get('HOSTNAME', '') or f"pid-{os.getpid()}"


# ── Hash derivation ─────────────────────────────────────────────────────────

def _normalize_timestamp(ts) -> str:
    """Render a Postgres TIMESTAMPTZ to a stable ISO 8601 string with explicit
    microsecond precision. Both workers reading the same row MUST produce the
    same hash, regardless of psycopg2 driver / datetime quirks. Always emit
    UTC offset (`+00:00`)."""
    if ts is None:
        return ''
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        # ISO 8601 with microseconds; '+00:00' offset.
        return ts.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
    return str(ts)


def hash_claim_key(collection: str, key: str, state: str, state_changed_at) -> str:
    """Deterministic SHA-256 of the (collection, key, state, state_changed_at)
    tuple. Returns 64-char hex. Both workers seeing the same row at the same
    state-entry moment compute the SAME hash — that's the basis for the
    INSERT ON CONFLICT atomic-claim guarantee."""
    parts = [
        config.FACTORY_NAME or '',
        collection,
        key,
        state,
        _normalize_timestamp(state_changed_at),
    ]
    blob = '|'.join(parts).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


# ── DB ops ─────────────────────────────────────────────────────────────────

def _claim_cursor():
    """Fresh cursor on the process-wide shared connection (teenyfactories.db).
    Same connection the dispatcher rides; no additional DB slots used."""
    return db.cursor()


def try_claim(collection: str, key: str, state: str, state_changed_at,
              ttl_seconds: float) -> bool:
    """Attempt to claim the (coll, key, state-cycle) tuple. Returns True if
    we acquired the claim, False if another worker already holds it OR the
    source row has moved on (state changed / state_changed_at advanced).

    Atomicity model — single statement, two race-closers:

    1. CTE `candidate` SELECTs the source factory_data row predicated on
       (factory_name, collection, key, state, state_changed_at) with
       `FOR UPDATE SKIP LOCKED`. Concurrent workers attempting the same row
       see SKIP LOCKED and yield 0 rows → INSERT inserts nothing → won=False.
    2. If the row has since moved on (handler completed, state advanced,
       state_changed_at bumped to T2), our predicate WHERE state_changed_at=T1
       no longer matches → 0 rows → INSERT inserts nothing → won=False. This
       closes the "stale snapshot re-claim" race where worker B polled the
       row at T1, worker A claimed+ran+released, and B then tried to claim
       the now-completed work.

    The CTE-and-INSERT is one statement → atomic. The PK ON CONFLICT remains
    as belt-and-braces against the (impossible) case where both SKIP LOCKED
    SELECTs somehow see the same row.

    RLS on factory_job_claims auto-scopes the INSERT to the current factory.
    """
    claim_key = hash_claim_key(collection, key, state, state_changed_at)
    claim_data = {
        'collection':              collection,
        'key':                     key,
        'source_state':            state,
        'source_state_changed_at': _normalize_timestamp(state_changed_at),
        'claimed_by':              _worker_id(),
    }
    try:
        cursor = _claim_cursor()
        cursor.execute(
            """
            WITH candidate AS (
                SELECT 1
                  FROM public.factory_data
                 WHERE factory_name     = %s
                   AND collection       = %s
                   AND key              = %s
                   AND state            = %s
                   AND state_changed_at = %s
                   FOR UPDATE SKIP LOCKED
            )
            INSERT INTO public.factory_job_claims
                (factory_name, claim_key, claim_data, lease_expires_at)
            SELECT %s, %s, %s::jsonb, NOW() + (%s * INTERVAL '1 second')
              FROM candidate
            ON CONFLICT (factory_name, claim_key) DO NOTHING
            RETURNING claim_key
            """,
            (
                # CTE params: source-row predicate
                config.FACTORY_NAME, collection, key, state, state_changed_at,
                # INSERT params: claim row
                config.FACTORY_NAME, claim_key, _json_dumps(claim_data),
                float(ttl_seconds),
            ),
        )
        row = cursor.fetchone()
        won = row is not None
        if won:
            log_debug(
                f"claim ACQUIRED {collection}/{key} state={state} "
                f"key={claim_key[:8]}… ttl={int(ttl_seconds)}s"
            )
        else:
            log_debug(
                f"claim SKIPPED {collection}/{key} state={state} "
                f"key={claim_key[:8]}… (held by another worker or source row moved on)"
            )
        return won
    except Exception as e:
        # Fail-closed: if the claim system is broken, skip the handler.
        # Better to halt than double-fire. Operator notices via logs.
        db.invalidate_if_dead(e)
        log_warn(
            f"claim INSERT failed for {collection}/{key} state={state}: {e} "
            f"— skipping handler to avoid potential double-fire"
        )
        return False


def release_claim(collection: str, key: str, state: str, state_changed_at) -> None:
    """Release our claim by DELETEing the row. Only deletes if claimed_by
    still matches our worker — protects against re-claiming after a TTL
    expiry where another worker took over."""
    claim_key = hash_claim_key(collection, key, state, state_changed_at)
    try:
        cursor = _claim_cursor()
        cursor.execute(
            """
            DELETE FROM public.factory_job_claims
             WHERE factory_name = %s
               AND claim_key    = %s
               AND claim_data->>'claimed_by' = %s
            """,
            (config.FACTORY_NAME, claim_key, _worker_id()),
        )
        log_debug(
            f"claim RELEASED {collection}/{key} state={state} key={claim_key[:8]}…"
        )
    except Exception as e:
        # Release failure is not fatal — janitor will reap on TTL.
        db.invalidate_if_dead(e)
        log_warn(
            f"claim DELETE failed for {collection}/{key} state={state}: {e} "
            f"— janitor will reap on lease expiry"
        )


def janitor_sweep_if_due() -> None:
    """Run the stale-claim reaper if 30s have elapsed since the last sweep.
    Called from `tf.run_pending()` on every tick — cheap fast-path returns
    immediately when not due.

    RLS auto-scopes the DELETE to the current factory. Multiple pods running
    concurrent sweeps is safe — DELETEs of already-deleted rows are no-ops.
    NOTIFYs on `tf_data_changed` after a reap so polling workers wake to
    pick up the now-claimable row.
    """
    global _last_janitor_tick
    now = time.monotonic()
    if now - _last_janitor_tick < _JANITOR_INTERVAL_SECONDS:
        return
    _last_janitor_tick = now

    try:
        cursor = _claim_cursor()
        cursor.execute(
            """
            DELETE FROM public.factory_job_claims
             WHERE lease_expires_at < NOW()
            RETURNING claim_data->>'collection' AS collection
            """,
        )
        reaped = cursor.fetchall()
        if not reaped:
            return
        log_warn(
            f"janitor reaped {len(reaped)} stale claim(s) — possible worker crash "
            f"or process death. Wake collections: "
            f"{sorted({r[0] for r in reaped if r and r[0]})}"
        )
        # Wake polling workers so reaped rows get re-picked-up even in
        # otherwise-idle factories. Use the same global channel run_pending
        # listens on.
        cursor.execute('NOTIFY tf_data_changed')
    except Exception as e:
        db.invalidate_if_dead(e)
        log_warn(f"janitor sweep failed (will retry on next tick): {e}")


# ── tiny json helper to avoid import cycle ──────────────────────────────────

def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, separators=(',', ':'))
