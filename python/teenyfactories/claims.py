"""Durable lease ownership, independent of notification delivery.

The datastore atomically checks the source state-entry and acquires its claim.
Commit the claim before invoking handlers; release only the current worker's
claim. Fail closed on acquire errors, and recover abandoned leases via janitor.
"""
import hashlib
import os
import time
from datetime import datetime, timezone

from . import config
from .datastore import get_datastore
from .logging import log_debug, log_warn

_JANITOR_INTERVAL_SECONDS = 30.0
_last_janitor_tick = 0.0
DEFAULT_CLAIM_DURATION_SECONDS = 3600.0


def _worker_id():
    return os.environ.get('HOSTNAME', '') or f'pid-{os.getpid()}'


def _normalize_timestamp(ts):
    if ts is None:
        return ''
    if isinstance(ts, datetime):
        ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
        return ts.strftime('%Y-%m-%dT%H:%M:%S.%f%z')
    return str(ts)


def hash_claim_key(collection, key, state, state_changed_at):
    """Keep the existing claim-key wire format across old/new workers."""
    parts = [config.FACTORY_NAME or '', collection, key, state, _normalize_timestamp(state_changed_at)]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def try_claim(collection, key, state, state_changed_at, ttl_seconds):
    try:
        won = get_datastore().try_claim(collection, key, state, state_changed_at, ttl_seconds)
        log_debug(f"claim {'ACQUIRED' if won else 'SKIPPED'} {collection}/{key} state={state}")
        return won
    except Exception as exc:
        log_warn(f'claim INSERT failed for {collection}/{key} state={state}: {exc} '
                 '— skipping handler to avoid potential double-fire')
        return False


def release_claim(collection, key, state, state_changed_at):
    try:
        get_datastore().release_claim(collection, key, state, state_changed_at)
        log_debug(f'claim RELEASED {collection}/{key} state={state}')
    except Exception as exc:
        log_warn(f'claim DELETE failed for {collection}/{key} state={state}: {exc} '
                 '— janitor will reap on lease expiry')


def janitor_sweep_if_due():
    global _last_janitor_tick
    now = time.monotonic()
    if now - _last_janitor_tick < _JANITOR_INTERVAL_SECONDS:
        return
    _last_janitor_tick = now
    try:
        reaped = get_datastore().reap_claims()
        if reaped:
            log_warn(f'janitor reaped {len(reaped)} stale claim(s) — possible worker crash '
                     f'or process death. Wake collections: {sorted({c for c in reaped if c})}')
    except Exception as exc:
        log_warn(f'janitor sweep failed (will retry on next tick): {exc}')
