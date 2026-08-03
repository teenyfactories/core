"""Pipeline-poll pub/sub over factory_data.

The model in one sentence: a subscribed ``(collection, state)`` IS a FIFO
queue; the handler consumes a row by transitioning its state or deleting
it; a row left in the state is re-dispatched, and after 5 non-departures
it is parked until the process restarts.

Single subscription surface — that's the entire factory-author API:

    @tf.on_state('orders', 'submitted').do
    def handle(item):
        ...                                  # MUST transition/remove the row
        tf.collection('orders').set(item['key'], state='processed', data=...)

    @tf.on_state('orders', 'submitted').delay(seconds=60).do
    def escalate(item): ...

There is no message bus. ``send_message`` / ``on_message`` / ``_messages``
were deleted — every flow is an explicit ``(collection, state)`` pipeline.

Contract: subscribing to ``(collection, state)`` means "process every row
currently in that state, oldest first." The HANDLER must move the row out
of the state on success (transition via ``tf.collection().set(...)`` or
delete via ``.remove()``). A row that stays put is re-dispatched on the
next poll. After ``_MAX_ATTEMPTS`` (5) non-departures — whether the handler
raised or returned cleanly without transitioning — the row is *parked*: it
is skipped (one ``log_error``) until the process restarts. A restart wipes
the in-memory strike map, so the row is re-attempted (a restart implies a
fix was shipped). A genuine rewrite of the row (which bumps ``updated_at``)
is treated as fresh work and resets the count.

Transport:
  * Dispatch is POLL-based. A poll scans ``factory_data`` for rows in each
    subscribed ``(collection, state)`` ordered by ``(state_changed_at, key)``.
  * NOTIFY on the single global ``tf_data_changed`` channel is an advisory
    wake ONLY — it never delivers or routes work. A poll pass runs when an
    own-factory NOTIFY was drained this tick, OR ``_SAFETY_POLL_INTERVAL_SEC``
    has elapsed since the last poll, OR it is the first tick. Otherwise
    ``run_pending()`` issues zero queries.
  * ``tf.sleep`` is a chunked wrapper around ``time.sleep`` that wakes on
    SIGTERM/SIGINT (slice ≈ 100 ms). Observed dispatch latency is still
    the factory author's own ``tf.sleep(N)`` loop cadence.

Lifecycle:
    1. Factory module imports tf and decorates handlers. Registrations are
       QUEUED — no DB connection opens at import time.
    2. First ``tf.run_pending()``: ``_first_tick_init`` drains pending
       registrations (opens connection, LISTENs ``tf_data_changed``), prints
       the banner, publishes the MCP catalog, then forces a first poll.
    3. Every tick: scheduled jobs → drain NOTIFY → poll if due.
"""

import collections as _collections
import time
from typing import Callable, Dict, List

import schedule as _schedule

from teenyfactories.config import FACTORY_NAME
from teenyfactories.lifecycle import (
    exit_if_shutting_down as _exit_if_shutting_down,
    install_signal_handlers as _install_signal_handlers,
)
from teenyfactories.logging import log_debug, log_info, log_warn, log_error


# =============================================================================
# Provider singleton + handler registry
# =============================================================================

_provider_instance = None

# Active handler registry, keyed by (collection, state). Each value is a list
# of entry dicts: { handler, delay_seconds }. One handler per (collection,
# state) is the supported contract (enforced with a log_warn at registration)
# — strike accounting is per-row, not per-entry.
_handlers: Dict[tuple, List[dict]] = {}

# Strike tracker: (key, state, state_changed_at_iso) -> attempt count, or
# _PARKED. Keyed on state_changed_at (NOT updated_at) so "the row moved" means
# the same thing here as it does for the poll, which orders and re-arms on
# state_changed_at: a transition to a new state OR a same-state re-queue that
# bumps state_changed_at both yield a NEW key and count as progress; a pure
# no-op keeps the key and accrues strikes. In-memory only — a restart clears it
# and the row is re-attempted. A row that departs its state is never returned by
# the poll again, so its entry is never re-touched and ages out via the
# insertion-order cap. Near-empty in healthy operation.
_strikes: "_collections.OrderedDict[tuple, int]" = _collections.OrderedDict()

# Last exception text per strike key, surfaced in the eventual park log
# (the park fires on the sighting AFTER the 5th dispatch). Evicted alongside
# _strikes.
_park_reason: Dict[tuple, str] = {}

# Strike keys whose handler has ACTUALLY executed at least once (claim acquired
# and handler called — whether it returned or raised). Lets the proactive no-op
# warning tell a genuine clean no-op (handler ran, row didn't move — warn) from a
# claim-skip where the handler never ran (see [tf:strike-on-sighting] — skips
# still count strikes). Evicted alongside _strikes.
_ran_keys: set = set()

_MAX_ATTEMPTS = 5
_PARKED = -1

# Most rows we remember failing at once. Insertion-order eviction (drop the
# oldest tracked entry when full) bounds memory in a long-running agent.
# Only bites if 2048+ distinct rows are broken simultaneously — already a
# catastrophic factory; a dropped still-failing entry just restarts its
# count from 0.
_RETRY_TRACKER_MAX = 2048

# Poll cadence floor. A poll runs at most this often when no own-factory
# NOTIFY arrives. Monotonic clock so wall-clock jumps don't reschedule.
_SAFETY_POLL_INTERVAL_SEC = 10.0
_last_poll_ts: float = 0.0

# Pending registrations queue. Subscriptions registered before the first
# run_pending() tick (the typical import-time case) AND during a dispatch
# (re-entrant case) land here, drained at safe lifecycle points.
_pending_registrations: List[dict] = []

# One-shot first-tick bootstrap flag.
_initialized = False


def _get_provider():
    """Get or create the PostgreSQL provider instance.

    No connect here — the provider rides the shared connection in
    ``teenyfactories.db``, opened lazily by whoever touches the DB first.
    """
    global _provider_instance
    if _provider_instance is None:
        from .providers.postgres import PostgresProvider
        _provider_instance = PostgresProvider()
    return _provider_instance


# =============================================================================
# on_state — the only subscription API
# =============================================================================

class SubscriptionBuilder:
    """Fluent builder for tf.on_state(...).

    Three optional chain modifiers, any order:
        @tf.on_state(collection, state).do(handler)
        @tf.on_state(collection, state).delay(seconds=N).do(handler)
        @tf.on_state(collection, state).claim_duration(hours=2).do(handler)
        @tf.on_state(collection, state).delay(seconds=5).claim_duration(minutes=30).do(handler)

    `.delay(seconds=N, minutes=N, hours=N)` defers dispatch until
    `state_changed_at + delta <= NOW()`. Strict cancellation — if the row
    leaves the watched state before the delay elapses it is never
    dispatched. Re-arm — a transition out and back in bumps state_changed_at
    and restarts the delay. Granularity is the `tf.run_pending()` cadence.
    Time units are additive: `.delay(seconds=30, minutes=2)` → 2m30s.

    `.claim_duration(seconds=N | minutes=N | hours=N)` — how long this
    subscription's claim on a row remains valid. If the worker dies mid-
    handler, the claim expires after this duration and another worker can
    pick the row up. Default 1 hour. Set smaller for tight crash recovery
    (accepts double-fire risk for fast retry); larger for handlers that
    legitimately run longer.
    """

    def __init__(self, collection: str, state: str):
        from ..claims import DEFAULT_CLAIM_DURATION_SECONDS
        self._collection = collection
        self._state = state
        self._delay_seconds: float = 0.0
        self._claim_duration_seconds: float = DEFAULT_CLAIM_DURATION_SECONDS

    def delay(self, seconds: float = 0, minutes: float = 0, hours: float = 0):
        delta = float(seconds) + float(minutes) * 60.0 + float(hours) * 3600.0
        if delta < 0:
            raise ValueError(f"delay must be non-negative, got {delta}")
        self._delay_seconds = delta
        return self

    def claim_duration(self, seconds: float = 0, minutes: float = 0, hours: float = 0):
        delta = float(seconds) + float(minutes) * 60.0 + float(hours) * 3600.0
        if delta <= 0:
            raise ValueError(f"claim_duration must be positive, got {delta}")
        self._claim_duration_seconds = delta
        return self

    def do(self, handler: Callable):
        _enqueue_registration(
            collection=self._collection,
            state=self._state,
            handler=handler,
            delay_seconds=self._delay_seconds,
            claim_duration_seconds=self._claim_duration_seconds,
        )
        return handler


def on_state(collection: str, state: str) -> SubscriptionBuilder:
    """Subscribe to (collection, state).

    Usage:
        @tf.on_state('documents', 'loaded').do
        def handle_loaded(item):
            # item = {factory_name, collection, key, user_id, data, state,
            #         created_at, updated_at}
            ...
            tf.collection('documents').set(item['key'], state='parsed', ...)

    Contract: every row currently in (collection, state), oldest first,
    fires the handler. Your handler MUST move the row out of the state on
    success (transition via `tf.collection(...).set(key, state='next', ...)`
    or delete via `.remove(key)`). A row that stays put is re-dispatched
    next poll; after 5 non-departures it is parked until restart.
    """
    return SubscriptionBuilder(collection, state)


# =============================================================================
# Registration queue — deferred LISTEN keeps DB I/O off the import path
# =============================================================================

def _enqueue_registration(collection: str, state: str, handler: Callable,
                          delay_seconds: float = 0.0,
                          claim_duration_seconds: float = 3600.0):
    """Queue a subscription. The LISTEN + handler-table insertion happens
    later, from inside the run_pending lifecycle (`_flush_registrations`)."""
    _pending_registrations.append({
        'collection':              collection,
        'state':                   state,
        'handler':                 handler,
        'delay_seconds':           delay_seconds,
        'claim_duration_seconds':  claim_duration_seconds,
    })


def _flush_registrations():
    """Drain the pending-registrations queue: open the connection if needed,
    LISTEN once on the single global `tf_data_changed` wake channel, and add
    each handler to `_handlers`. Idempotent. Warns loudly if a second handler
    is registered on an existing (collection, state) — strike accounting is
    per-row not per-handler, so two handlers on one state would duplicate
    execution of the one that succeeds.
    """
    if not _pending_registrations:
        return

    from .providers.postgres import TF_DATA_CHANGED_CHANNEL

    provider = _get_provider()
    try:
        provider.listen(TF_DATA_CHANGED_CHANNEL)  # idempotent in the provider
    except Exception as e:
        log_error(f"LISTEN {TF_DATA_CHANGED_CHANNEL} failed: {e}")

    while _pending_registrations:
        reg = _pending_registrations.pop(0)
        coll = reg['collection']
        state = reg['state']
        key = (coll, state)
        delay_seconds = reg.get('delay_seconds') or 0.0

        if _handlers.get(key):
            log_warn(
                f"Second handler registered on ({coll!r}, {state!r}). Only ONE "
                f"handler per (collection, state) is supported: strike/retry "
                f"accounting is per-row, not per-handler, so multiple handlers "
                f"on one state silently duplicate-execute the succeeding one. "
                f"Refactor to a single handler."
            )

        _handlers.setdefault(key, []).append({
            'handler':                reg['handler'],
            'delay_seconds':          delay_seconds,
            'claim_duration_seconds': reg.get('claim_duration_seconds') or 3600.0,
        })
        log_debug(
            f"Registered handler for {coll}.{state} (delay_seconds={delay_seconds})"
        )


# =============================================================================
# Lifecycle: first-tick init + run_pending
# =============================================================================

def _first_tick_init():
    """One-shot bootstrap, called the first time `run_pending()` runs.

    Drains registrations (issues the LISTEN) BEFORE the first poll — so a
    write that lands between connect and the first poll is still caught.
    """
    global _initialized
    if _initialized:
        return

    _log_startup_banner()
    _flush_registrations()

    try:
        from teenyfactories.mcp import _maybe_publish_mcp
        _maybe_publish_mcp()
    except Exception as e:
        log_error(f"MCP catalog publish failed (continuing): {e}")

    _initialized = True


def _log_startup_banner():
    """Single-line provenance banner at first run_pending(). Debug-level —
    operators don't normally need this; surfaces under --log-level=debug."""
    try:
        from teenyfactories.__version__ import (
            __version__, __build_sha__, __build_date__,
        )
        from teenyfactories.config import FACTORY_NAME, AGENT_NAME
        log_debug(
            f"teenyfactories {__version__} "
            f"(build {__build_sha__} {__build_date__}) — "
            f"agent={AGENT_NAME!r} factory={FACTORY_NAME!r}"
        )
    except Exception as e:
        log_error(f"startup banner failed (continuing): {e}")


def run_pending():
    """Drain scheduled jobs, drain NOTIFY, poll if due.

    Factories call this in a loop:
        while True:
            tf.run_pending()
            tf.sleep(1)

    `tf.sleep` wakes on SIGTERM/SIGINT (chunked under the hood); SIGTERM
    + SIGINT handlers are installed on the first call here. When a signal
    arrives the flag is observed at the end of this function and at the
    next `tf.sleep` slice, raising SystemExit(0) for clean teardown.

    A poll runs only when an own-factory NOTIFY was drained, OR
    _SAFETY_POLL_INTERVAL_SEC has elapsed, OR it is the first tick.
    Otherwise this issues zero queries.
    """
    global _last_poll_ts

    # Idempotent — only the first call actually registers the signals.
    _install_signal_handlers()

    first = not _initialized
    if not _initialized:
        _first_tick_init()
    elif _pending_registrations:
        _flush_registrations()

    import traceback as _tb

    # Scheduled jobs run every tick, OUTSIDE the poll gate.
    try:
        _schedule.run_pending()
    except Exception as e:
        log_error(f"Scheduled job raised: {e}\n{_tb.format_exc()}")

    notify_hit = False
    try:
        notify_hit = _drain_notifications()
    except Exception as e:
        log_error(f"NOTIFY drain raised: {e}\n{_tb.format_exc()}")

    now = time.monotonic()
    should_poll = (
        first
        or notify_hit
        or (now - _last_poll_ts >= _SAFETY_POLL_INTERVAL_SEC)
    )
    if should_poll:
        # Spend-limit enforcement no longer lives at the poll gate. Cost is
        # owned by the orchestrator (computed at read; limits enforced via an
        # HTTP clearance check tf makes BEFORE each LLM call — see
        # teenyfactories/cost_clearance.py, gated inside tf.call_llm). The poll
        # loop does no cost work.
        try:
            _poll_pass()
        except Exception as e:
            log_error(f"Poll pass raised: {e}\n{_tb.format_exc()}")
        _last_poll_ts = time.monotonic()

    # Claim janitor — reap stale claims past their lease_expires_at. No-op
    # fast-path when the 30s interval hasn't elapsed. RLS auto-scopes to
    # the caller's factory; concurrent sweeps from sibling pods are
    # idempotent. NOTIFYs after a reap so polling workers wake to re-pick
    # rows whose holders died.
    try:
        from ..claims import janitor_sweep_if_due
        janitor_sweep_if_due()
    except Exception as e:
        log_error(f"Claim janitor raised: {e}\n{_tb.format_exc()}")

    # SIGTERM/SIGINT received during this tick? Raise SystemExit now so
    # the user's `while True` exits cleanly via Python's normal teardown
    # (atexit hooks, finalisers). The next `tf.sleep` slice would catch
    # it too, but exiting here is more responsive.
    _exit_if_shutting_down()


# =============================================================================
# Dispatch core — strike state machine
# =============================================================================

def _iso(ts) -> str:
    """Stable string form of a timestamp for the strike key."""
    if ts is None:
        return ''
    if hasattr(ts, 'isoformat'):
        return ts.isoformat()
    return str(ts)


def _evict_strikes():
    """Insertion-order cap. Assignment to an existing OrderedDict key does
    NOT reorder it, so eviction drops the oldest first-seen entry."""
    while len(_strikes) > _RETRY_TRACKER_MAX:
        old_key, _ = _strikes.popitem(last=False)
        _park_reason.pop(old_key, None)
        _ran_keys.discard(old_key)


def _dispatch(entries: List[dict], item: dict):
    """Fire the handler(s) for one row and advance its strike count.

    A row re-seen still in the state on a later poll proves the prior
    attempt didn't move it — exceptions and clean-but-no-op are counted
    identically. After _MAX_ATTEMPTS sightings the row is parked.
    """
    coll = item.get('collection')
    state = item.get('state')
    if coll is None or state is None:
        return

    rk = (item.get('key') or '', state, _iso(item.get('state_changed_at')))
    n = _strikes.get(rk)

    if n == _PARKED:
        return  # already gave up; silent until restart

    if n is None:
        _strikes[rk] = 1
    elif n < _MAX_ATTEMPTS:
        _strikes[rk] = n + 1
        # Proactive no-op warning. A re-sighting with the SAME strike key proves
        # the previous dispatch left the row exactly where it was — same state AND
        # same state_changed_at. (A re-queue bounce bumps state_changed_at → a NEW
        # key → treated as progress, never flagged here.) Warn ONCE, on the first
        # re-sighting (n == 1), and only for a CLEAN no-op where the handler
        # actually ran: an exception was already logged (recorded in _park_reason),
        # and a claim-skip never ran the handler (rk not in _ran_keys).
        if n == 1 and rk in _ran_keys and rk not in _park_reason:
            log_warn(
                f"Handler {coll}.{state} returned without advancing key={rk[0]!r} "
                f"(state and state_changed_at unchanged) — it will re-fire and park "
                f"after {_MAX_ATTEMPTS} attempts. Transition the row to a new state, or "
                f"tf.collection({coll!r}).remove(...); if it aggregates many rows, it "
                f"belongs on tf.on_schedule, not a per-row on_state handler."
            )
    else:
        # n == _MAX_ATTEMPTS: this sighting is one past the 5th dispatch.
        _strikes[rk] = _PARKED
        _ran_keys.discard(rk)
        reason = _park_reason.pop(rk, None) or "handler did not transition the row"
        log_error(
            f"Giving up after {_MAX_ATTEMPTS} attempts; row parked in "
            f"{coll}.{state} key={rk[0]!r}: {reason}"
        )
        return

    _evict_strikes()
    attempt = _strikes[rk]

    # Stepped-debug auto-halt. No-op when factory debug mode is off.
    # _auto_halt blocks until operator clicks Continue (or disables mode).
    # Direct submodule import — `teenyfactories.breakpoint` is the public
    # FUNCTION (re-exported in __init__.py); `teenyfactories.breakpoint_mod`
    # would be cleaner but we use the explicit submodule path here instead.
    from ..breakpoint import _auto_halt as _bp_auto_halt
    _bp_auto_halt(coll, state, item)

    # Atomic claim wrap — guards against double-fire from replicas / orphan
    # pods / rolling-restart races. Always-on (not env-gated). For details
    # see core/python/teenyfactories/claims.py.
    from ..claims import try_claim, release_claim
    row_key = item.get('key')
    sca = item.get('state_changed_at')

    for entry in entries:
        ttl = entry.get('claim_duration_seconds') or 3600.0
        if not try_claim(coll, row_key, state, sca, ttl):
            # Another worker (replica/orphan/zombie) holds the claim. Silently
            # skip — don't strike-count, because we didn't actually attempt the
            # handler. The other worker will or won't transition the row; if
            # it doesn't, we'll see the row again on next poll and try-claim
            # afresh (claim row gone after their release, or expired via janitor).
            continue
        # Claim held → the handler runs. Mark the strike key so a later re-sighting
        # can tell this genuine attempt from a claim-skip (proactive-warn guard).
        _ran_keys.add(rk)
        try:
            entry['handler'](item)
        except Exception as e:
            _park_reason[rk] = repr(e)
            log_error(
                f"Handler {coll}.{state} failed on key={rk[0]!r} "
                f"attempt {attempt}/{_MAX_ATTEMPTS}: {e}"
            )
        finally:
            release_claim(coll, row_key, state, sca)


# =============================================================================
# Poll pass + NOTIFY drain
# =============================================================================

def _poll_pass():
    """One inline FIFO pass over every subscribed (collection, state).

    Live (non-delayed) entries share one `fetch_rows` scan. Each delayed
    entry runs its own `fetch_due_rows` (the delay is a SQL predicate, no
    cursor). In-retry rows interleave in natural state_changed_at position
    (accepted head-of-line tradeoff; slow-failing handlers should set
    their own I/O timeouts).
    """
    if not _handlers:
        return
    provider = _get_provider()
    for (coll, state), entries in list(_handlers.items()):
        live = [e for e in entries if not (e.get('delay_seconds') or 0.0)]
        if live:
            for item in provider.fetch_rows(coll, state):
                _dispatch(live, item)
        for entry in entries:
            d = entry.get('delay_seconds') or 0.0
            if d <= 0:
                continue
            for item in provider.fetch_due_rows(coll, state, d):
                _dispatch([entry], item)


def _drain_notifications() -> bool:
    """Drain the psycopg2 NOTIFY buffer every tick (so it can't grow).

    Returns True iff ≥1 drained NOTIFY was for THIS factory. The payload is
    consulted for `factory_name` ONLY — collection/state come from the DB
    query in `_poll_pass`, never from the payload. (The trigger payload key
    for state is `state_after`, not `state`; do not reintroduce any
    `payload['state']` read here.) Cross-factory NOTIFYs on the shared
    `tf_data_changed` channel are drained but do not trigger a poll.
    """
    provider = _get_provider()
    notifications = provider.poll_notifications()
    if not notifications:
        return False
    hit = False
    for note in notifications:
        payload = note.get('payload') or {}
        if isinstance(payload, dict) and payload.get('factory_name') == FACTORY_NAME:
            hit = True
    return hit
