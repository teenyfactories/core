"""
Stepped debugging primitive.

Per-factory toggle: `factory_data._debug.mode` row's `state` column carries
the scope:

    'all'      — halt at every (collection,state) dispatch AND at every
                 explicit `tf.breakpoint()` call
    'explicit' — halt only at explicit `tf.breakpoint()` calls (auto pre-job
                 halts skipped). Useful for stepping inside a single handler
                 without halting on every queue dispatch.
    'disabled' — off (also represented by an absent row)

State remains the single source of truth — `value` carries audit metadata
(enabled_at, by, etc.) only.

Public API:
    tf.breakpoint(message: str) -> None
        Cheap no-op when scope is 'disabled'/absent. Otherwise INSERTs a
        factory_logs row with level='breakpoint' and log_data._debug=
        {state:'waiting', ...}, then polls that row every 1s until
        log_data._debug.state == 'continued' or scope flips off.

Internals (private, not exported):
    _debug_mode_scope()           — 1s TTL cached, returns 'all'|'explicit'|None
    _debug_mode_scope_uncached()  — bypass cache (used by _wait_for_release)
    _log_breakpoint(message, ...) — INSERT ... RETURNING id; direct psycopg2
    _wait_for_release(log_id)     — 1s poll loop
    _auto_halt(coll, state, item) — used by message_queue._dispatch; only
                                    halts when scope == 'all'

Reserved collection name: `_debug`. User code MUST NOT write rows in this
collection — use `tf.breakpoint()` and the orchestrator's debug endpoints.

Direct psycopg2 path (not the logging stdlib pipeline) is required so the
INSERT can carry `RETURNING id` — the agent needs the bigserial id back to
poll its own row. Tagged so future audits don't flag it as paradigm bypass.
"""

import json
import time
from typing import Optional

from . import config


# ── Mode cache ─────────────────────────────────────────────────────────────
# 1s TTL keeps per-tick cost flat (one cached read per dispatch) while
# letting an enable take effect within 1s. `_wait_for_release` bypasses the
# cache so disabling a mid-halt mode releases promptly.
_VALID_SCOPES = ('all', 'explicit')
_mode_cache = {'value': None, 'expires_at': 0.0}


def _debug_mode_scope_uncached() -> Optional[str]:
    """Read factory_data._debug.mode directly. No cache.

    Returns 'all', 'explicit', or None (off / absent / invalid).
    """
    from .collection import collection as _collection  # lazy
    try:
        row = _collection('_debug').get('mode')
    except Exception:
        return None
    if row is None:
        return None
    scope = row.get('state')
    return scope if scope in _VALID_SCOPES else None


def _debug_mode_scope() -> Optional[str]:
    """1s TTL cached read of the factory's debug-mode scope."""
    now = time.monotonic()
    if now < _mode_cache['expires_at']:
        return _mode_cache['value']
    scope = _debug_mode_scope_uncached()
    _mode_cache['value'] = scope
    _mode_cache['expires_at'] = now + 1.0
    return scope


# ── Breakpoint row INSERT (direct DB write) ─────────────────────────────────
# Not a paradigm violation — we need RETURNING id so the agent can poll its
# own row. The logging stdlib pipeline (PostgresLogHandler) is fire-and-
# forget with no return value. See _log_breakpoint docstring.

def _log_breakpoint(message: str, *, kind: str, **context) -> Optional[int]:
    """
    INSERT a factory_logs row with level='breakpoint' and
    log_data._debug={state:'waiting', kind, ...context}. Returns the row's
    bigserial id so the caller can poll it via _wait_for_release.

    Direct psycopg2 path (RETURNING id is not exposed via PostgresLogHandler).
    Returns None on any failure — caller treats None as "mode-off behavior";
    no halt happens.

    `kind` is 'auto' (pre-dispatch halt) or 'explicit' (tf.breakpoint() call).
    `context` carries auto-halt locator fields (coll, state, row_key).
    """
    try:
        conn = config.connect_postgres()
        log_data = {
            '_debug': {
                'state': 'waiting',
                'kind': kind,
                'agent_name': config.AGENT_NAME,
                'container_id': config.AGENT_ID or None,
                'hit_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                **context,
            }
        }
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO factory_logs
                       (factory_name, service_name, container_id,
                        user_id, level, message, log_data)
                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                   RETURNING id""",
                (
                    config.FACTORY_NAME,
                    config.AGENT_SLUG or config.AGENT_NAME or None,
                    config.AGENT_ID or None,
                    'system',
                    'breakpoint',
                    message,
                    json.dumps(log_data),
                ),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


# ── Polling loop ───────────────────────────────────────────────────────────

def _wait_for_release(log_id: int) -> None:
    """
    Poll factory_logs row by id every 1s. Return when:
      - log_data._debug.state == 'continued', OR
      - scope flipped off (uncached check so disable releases promptly).
    """
    while True:
        try:
            conn = config.connect_postgres()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT log_data FROM factory_logs WHERE id = %s",
                    (log_id,),
                )
                row = cur.fetchone()
        except Exception:
            return  # fail-open: DB blip during halt shouldn't wedge the agent

        if row is None:
            return  # row deleted out from under us; treat as released
        log_data = row[0] or {}
        debug = log_data.get('_debug') or {}
        if debug.get('state') == 'continued':
            return

        # Uncached scope check — disabling mode mid-halt must release fast.
        if _debug_mode_scope_uncached() is None:
            return

        time.sleep(1.0)


# ── Public + internal halt entry points ─────────────────────────────────────

def breakpoint(message: str) -> None:  # noqa: A001 — shadowing builtin is intentional on tf namespace
    """
    Explicit breakpoint. No-op when factory debug mode is off (cheap; safe
    to leave in production code).

    Fires when scope is 'all' OR 'explicit' (both surface explicit halts).

    When firing: writes a factory_logs row, then blocks this agent until
    the operator clicks Continue (or disables the mode).

    The `message` is written to factory_logs and visible to anyone with
    logs-read access. Do NOT include secrets.
    """
    if _debug_mode_scope() not in _VALID_SCOPES:
        return
    log_id = _log_breakpoint(message, kind='explicit')
    if log_id is None:
        return  # write failed; don't wedge
    _wait_for_release(log_id)


def _auto_halt(coll: str, state: str, item: dict) -> None:
    """
    Pre-dispatch halt. Called from message_queue._dispatch immediately before
    invoking a handler. Fires only when scope == 'all'. 'explicit' scope
    skips auto-halts; 'disabled'/absent: no-op.

    item is the factory_data row dict from the poll query (has 'key').
    """
    if _debug_mode_scope() != 'all':
        return
    row_key = item.get('key') if isinstance(item, dict) else None
    msg = f"pre-handler halt: {coll}.{state} key={row_key}"
    log_id = _log_breakpoint(msg, kind='auto', coll=coll, state=state, row_key=row_key)
    if log_id is None:
        return
    _wait_for_release(log_id)
