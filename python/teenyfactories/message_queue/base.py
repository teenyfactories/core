"""Message queue: LISTEN/NOTIFY-backed pub/sub over factory_data.

Everything flows through `factory_data`. Messages live in the `_messages`
collection (state = topic, key = uuid); lifecycle items live in normal
collections where `state` advances through the pipeline.

Two subscription shapes — that's the entire factory-author surface:

    @tf.on_state('orders', 'submitted').do
    def handle(item): ...

    @tf.on_state('orders', 'submitted').delay(seconds=60).do
    def escalate(item): ...

`tf.on_message('topic')` is shorthand for `tf.on_state('_messages', 'topic')`.

Contract: subscribing to (collection, state) means "process every row
currently in that state, plus every new arrival/transition into it." The
HANDLER is responsible for moving the row out of state on success — either
by transitioning to a different state via `tf.collection().set(key, ...)`
or by deleting it via `.remove(key)`. If the handler doesn't transition,
the row stays in scope and the safety poll will re-fire it every 10s.
That's a feature for idempotent aggregators; a bug for everything else.

Transport is a hybrid:
  * LISTEN on hashed channel `tf_state_<md5(factory.collection.state)>` —
    primary, low-latency path.
  * 10s safety poll over (updated_at, key) cursor — catches anything LISTEN
    missed (network blip, slow consumer, restart). A non-zero result emits
    log_warn so operators see NOTIFY drops.
  * Per-handler dedupe LRU prevents the same (key, updated_at) firing twice
    when the safety poll and LISTEN both pick up the same row.

Cursor: per (collection, state), inits to epoch on first registration so
the first safety-poll tick naturally processes every existing row in the
state. Advances past every dispatched row via `(updated_at, key)` strict
ordering — composite tuple to handle equal-timestamp rows correctly.

Delay flag: `.delay(seconds=N)` defers dispatch until `updated_at + N <=
now()`. Strict cancellation — if the row leaves the watched state before
the delay elapses, the handler is skipped. Re-arm — on transition out and
back in, `updated_at` bumps and the delay restarts.

Lifecycle:
    1. Factory module imports tf and decorates handlers with `@tf.on_state(...)
       .do(...)`. Registrations are QUEUED — no DB connection opens at import
       time.
    2. Factory calls `tf.run_pending()` for the first time. The loop:
         a. Runs `_first_tick_init()` once: drains pending registrations
            (opens connection, LISTENs per hashed channel), prints the
            startup banner, publishes the MCP catalog.
         b. Runs scheduled jobs.
         c. Runs the safety poll if 10s have elapsed since the last one.
         d. Runs delayed dispatch (per-tick query, no cadence floor).
         e. Drains LISTEN/NOTIFY queue and dispatches to handlers.
    3. Subsequent calls to `run_pending()` skip step 2a (idempotent) but
       pick up any registrations made AFTER the first tick (re-entrant
       case) — those are flushed at the top of dispatch.
"""

import collections as _collections
import time
from datetime import datetime as _datetime, timezone as _timezone
from typing import Any, Callable, Dict, List, Tuple

import schedule as _schedule

from teenyfactories.config import FACTORY_NAME
from teenyfactories.logging import log_info, log_warn, log_error


# =============================================================================
# Provider singleton + handler registry
# =============================================================================

_provider_instance = None

# Active handler registry, keyed by (collection, state).
# Each value is a list of dicts: { handler, replay_mode, latest_only, replayed }.
# replay_mode: None | 'latest' | 'all'.
_handlers: Dict[tuple, List[dict]] = {}

# Per-(collection, state) cursor — composite (updated_at, key) tuple. Advanced
# by every successful dispatch path (startup replay, LISTEN drain, safety
# poll). Cursor is per-key, NOT per-handler — multiple handlers on the same
# (collection, state) share LISTEN, share cursor, share dedupe state.
_cursors: Dict[tuple, Tuple[Any, str]] = {}

# Recently-dispatched dedupe LRU is now per-handler-entry (lives on the
# entry dict in _handlers). Prevents the same row firing twice on the same
# handler when safety-poll and LISTEN both pick it up. Key: (row_key,
# updated_at_iso). FIFO-evict at _DEDUPE_LRU_CAP. The dedupe key uses
# updated_at so legitimate re-writes to the same row key (which bump
# updated_at) DO re-fire.
_DEDUPE_LRU_CAP = 256

# Safety-poll cadence — runs once per 10s, gated inside run_pending. Use
# monotonic time so wall-clock jumps don't reschedule.
_SAFETY_POLL_INTERVAL_SEC = 10.0
_last_safety_poll_ts: float = 0.0

# Once-per-process md5-collision warn rate-limit. Cache `(channel,
# expected_collection, expected_state)` triples seen — log_error fires only
# on first occurrence per process, prevents log floods if a real collision
# (or a misconfigured trigger) ever fires.
_collision_warned: set = set()

# Per-(collection, state) "first safety-poll tick done" set. The first tick
# after registration intentionally processes every existing row in the state
# (cursor=epoch) — that's the documented startup dispatch path, not a
# NOTIFY drop. Suppressing the warn on the first tick keeps the "safety poll
# caught N rows that LISTEN missed" log line meaningful: it ONLY fires when
# NOTIFY actually dropped events.
_safety_poll_seeded: set = set()

# Pending registrations queue. Subscriptions registered before the first
# `run_pending()` tick (the typical case at module import time) AND any
# subscriptions registered during a handler dispatch (re-entrant case) land
# here first. They are drained — LISTEN issued, entry added to `_handlers`
# — at safe points in the lifecycle (first-tick init, then top of every
# subsequent run_pending). This removes the race between LISTEN-during-
# dispatch and the connection's poll cursor.
_pending_registrations: List[dict] = []

# One-shot initialisation flag for the lifecycle's first tick. Promotes the
# implicit "first run_pending bootstraps everything" behaviour to an explicit
# state machine.
_initialized = False


# Sentinel used as the "epoch" cursor for replay-mode registrations. Pairs
# with the (updated_at, key) composite cursor used by fetch_rows_since /
# fetch_due_rows — strict-greater-than on this baseline returns every
# existing row in the (collection, state).
_EPOCH = _datetime(1970, 1, 1, tzinfo=_timezone.utc)


def _get_provider():
    """Get or create the PostgreSQL provider instance."""
    global _provider_instance
    if _provider_instance is None:
        from .providers.postgres import PostgresProvider
        _provider_instance = PostgresProvider()
        _provider_instance.connect()
        log_info("Connected to PostgreSQL message queue")
    return _provider_instance


# =============================================================================
# send_message — writes a row to factory_data._messages
# =============================================================================

class MessageSendBuilder:
    """Fluent builder for tf.send_message('topic').with_data({})"""

    def __init__(self, topic: str):
        self._topic = topic

    def with_data(self, payload: dict = None):
        return _do_send(self._topic, payload)


def send_message(topic: str):
    """
    Publish a fire-and-forget message. Becomes a row in factory_data with
    collection='_messages' and state=topic.

    Usage:
        tf.send_message('data_ready').with_data({'status': 'completed'})
    """
    return MessageSendBuilder(topic)


def _do_send(topic: str, payload: dict = None):
    """Internal: insert the message row via tf.collection()."""
    try:
        # Lazy import — collection imports message_queue (this module) for
        # the NOTIFY trigger registration; importing eagerly would cycle.
        from teenyfactories.collection import collection
        collection('_messages').add(state=topic, data=payload or {})
        log_info(f"send_message published to _messages.{topic}")
        return True
    except Exception as e:
        log_error(f"send_message failed for topic {topic}: {e}")
        return False


# =============================================================================
# on_state — primary subscription API
# =============================================================================

class SubscriptionBuilder:
    """
    Fluent builder for tf.on_state(...) and tf.on_message(...).

    Two shapes:
        @tf.on_state(collection, state).do(handler)
        @tf.on_state(collection, state).delay(seconds=N).do(handler)

    `.delay(seconds=N, minutes=N, hours=N)` defers dispatch by the given
    delta. Strict cancellation — if the row leaves the watched state
    before the delay elapses, the handler is skipped. Re-arm — on
    transition out and back in, `updated_at` bumps and the delay restarts.
    Granularity is the `tf.run_pending()` cadence (typically 1s);
    sub-second delays don't make sense in this model. Time units are
    additive within a single call: `.delay(seconds=30, minutes=2)` → 2m30s.
    """

    def __init__(self, collection: str, state: str):
        self._collection = collection
        self._state = state
        self._delay_seconds: float = 0.0

    def delay(self, seconds: float = 0, minutes: float = 0, hours: float = 0):
        delta = float(seconds) + float(minutes) * 60.0 + float(hours) * 3600.0
        if delta < 0:
            raise ValueError(f"delay must be non-negative, got {delta}")
        self._delay_seconds = delta
        return self

    def do(self, handler: Callable):
        _enqueue_registration(
            collection=self._collection,
            state=self._state,
            handler=handler,
            delay_seconds=self._delay_seconds,
        )
        return handler


def on_state(collection: str, state: str) -> SubscriptionBuilder:
    """
    Subscribe to (collection, state).

    Usage:
        @tf.on_state('documents', 'loaded').do
        def handle_loaded(item):
            # item = {factory_name, collection, key, user_id, data, state,
            #         created_at, updated_at}
            ...

    Contract: every row currently in (collection, state) AND every new
    row arriving in it will fire the handler. Your handler MUST move
    the row out of the state on success (transition via
    `tf.collection(...).set(key, state='next', ...)` or delete via
    `.remove(key)`); otherwise the safety poll re-fires it every 10s.
    """
    return SubscriptionBuilder(collection, state)


def on_message(topic: str) -> SubscriptionBuilder:
    """Pure shorthand for `tf.on_state('_messages', topic)`."""
    return on_state('_messages', topic)


# =============================================================================
# Registration queue — deferred LISTEN avoids cursor races
# =============================================================================

def _enqueue_registration(collection: str, state: str, handler: Callable,
                          delay_seconds: float = 0.0):
    """
    Queue a subscription registration. The actual LISTEN + handler-table
    insertion happens later, when `_flush_registrations()` is called from
    inside the run_pending lifecycle. Defers all DB I/O off the import path
    and isolates LISTEN from the connection's poll cursor.

    Channel name is computed at flush time (needs the provider import),
    not here — keeps imports light.
    """
    _pending_registrations.append({
        'collection':    collection,
        'state':         state,
        'handler':       handler,
        'delay_seconds': delay_seconds,
    })


def _flush_registrations():
    """
    Drain the pending-registrations queue: open the provider connection if
    needed, issue LISTEN once per distinct hashed channel, add each handler
    to the active `_handlers` registry, and init the per-(collection,state)
    cursor at epoch so the first safety-poll tick processes every existing
    row in the state. Idempotent.
    """
    if not _pending_registrations:
        return

    from .providers.postgres import hash_state_channel

    provider = _get_provider()
    listened: set = set()  # avoid duplicate LISTEN within one flush

    while _pending_registrations:
        reg = _pending_registrations.pop(0)
        coll = reg['collection']
        state = reg['state']
        key = (coll, state)

        channel = hash_state_channel(FACTORY_NAME, coll, state)
        if channel not in listened:
            try:
                provider.listen(channel)
                listened.add(channel)
            except Exception as e:
                log_error(f"LISTEN {channel} failed: {e}")
                continue

        # Cursor inits to epoch on first registration for this (collection,
        # state) so the first safety-poll tick naturally processes every
        # existing row. Subsequent registrations on the same key inherit
        # the cursor as it advances.
        if key not in _cursors:
            _cursors[key] = (_EPOCH, '')

        # Delayed handlers get their own cursor, also at epoch — same
        # rationale, the delay query just adds an `updated_at + delay <=
        # NOW()` filter on top.
        delay_seconds = reg.get('delay_seconds') or 0.0
        delay_cursor = (_EPOCH, '') if delay_seconds > 0 else None

        _handlers.setdefault(key, []).append({
            'handler':       reg['handler'],
            'delay_seconds': delay_seconds,
            'delay_cursor':  delay_cursor,
            # Per-entry dedupe LRU — prevents double-fire on this specific
            # handler when LISTEN + safety-poll race for the same row.
            'seen':          _collections.OrderedDict(),
        })
        log_info(
            f"Registered handler for {coll}.{state} (delay_seconds={delay_seconds})"
        )


# =============================================================================
# Lifecycle: first-tick init + run_pending
# =============================================================================

def _first_tick_init():
    """
    One-shot bootstrap. Called the first time `run_pending()` runs.

    Replaces the previous implicit pattern where `run_pending()` always
    called `_maybe_publish_mcp()` from inside a tight loop. The cost of
    that import + check fired on every tick AND the ordering was implicit
    — tools registered after the first tick were silently never published
    to the MCP catalog. The explicit init removes both surprises.
    """
    global _initialized
    if _initialized:
        return

    _log_startup_banner()
    _flush_registrations()

    # Publish the MCP catalog once. Lazy import to avoid module-load cycle
    # (mcp imports message_queue for the listen channel registration).
    try:
        from teenyfactories.mcp import _maybe_publish_mcp
        _maybe_publish_mcp()
    except Exception as e:
        log_error(f"MCP catalog publish failed (continuing): {e}")

    _initialized = True


def _log_startup_banner():
    """Emit a single-line provenance banner at first run_pending().

    Build SHA + date are baked into the agent image at `docker build` time
    (see `core/python/build.sh`). Falls back to 'dev' for `pip install -e`
    local runs where the env vars aren't set.
    """
    try:
        from teenyfactories.__version__ import (
            __version__, __build_sha__, __build_date__,
        )
        from teenyfactories.config import FACTORY_NAME, AGENT_NAME
        log_info(
            f"teenyfactories {__version__} "
            f"(build {__build_sha__} {__build_date__}) — "
            f"agent={AGENT_NAME!r} factory={FACTORY_NAME!r}"
        )
    except Exception as e:
        # Banner failure must NEVER block bootstrap. Log + continue.
        log_error(f"startup banner failed (continuing): {e}")


def run_pending():
    """Drain scheduled jobs, replay pending subscriptions, run safety poll, dispatch notifications.

    Factories call this in a loop:
        while True:
            tf.run_pending()
            tf.sleep(1)

    The first call also runs the lifecycle bootstrap (LISTEN registrations,
    MCP catalog publish). Subsequent calls flush any registrations made
    since the previous tick (re-entrant case) before dispatching, so a
    handler that registers another handler doesn't race with the cursor.
    """
    if not _initialized:
        _first_tick_init()
    elif _pending_registrations:
        # Re-entrant or post-init registrations — flush before dispatch.
        _flush_registrations()

    # Catch-all: any unhandled exception from a scheduled job, replay handler,
    # safety poll, or NOTIFY dispatcher must NOT kill the agent's main loop.
    # Log to factory_logs so the operator sees the failure, then continue.
    # Pubsub paths already wrap each handler individually; this is the outer
    # net for everything else (notably scheduled-job exceptions, which the
    # upstream `schedule` library propagates by default).
    import traceback as _tb
    try:
        _schedule.run_pending()
    except Exception as e:
        log_error(f"Scheduled job raised: {e}\n{_tb.format_exc()}")
    try:
        _run_safety_poll_if_due()
    except Exception as e:
        log_error(f"Safety poll raised: {e}\n{_tb.format_exc()}")
    try:
        _run_delayed_dispatch()
    except Exception as e:
        log_error(f"Delayed dispatch raised: {e}\n{_tb.format_exc()}")
    try:
        _drain_notifications()
    except Exception as e:
        log_error(f"NOTIFY dispatch raised: {e}\n{_tb.format_exc()}")


# =============================================================================
# Dispatch core — dedupe LRU + cursor advancement shared across paths
# =============================================================================

def _row_sort_key(row: dict) -> tuple:
    """Composite sort key matching the (updated_at, key) cursor."""
    return (row.get('updated_at'), row.get('key') or '')


def _dispatch_to_entries(entries: List[dict], item: dict, source: str):
    """Fire the handlers for one row, honouring per-entry dedupe + advancing
    the shared per-(coll, state) cursor.

    `source` is 'replay' | 'listen' | 'poll' — used in failure logs only.

    Each entry has its own `seen` LRU. A given (row_key, updated_at) won't
    fire the same entry twice across the LISTEN/safety-poll race window.
    Different entries on the same (coll, state) each track independently —
    so registering two handlers on the same key (mixed replay shapes, etc)
    doesn't suppress one of them.
    """
    coll = item.get('collection')
    state = item.get('state')
    if coll is None or state is None:
        return
    key = (coll, state)

    dedupe_key = (item.get('key') or '', _iso(item.get('updated_at')))

    fired_any = False
    for entry in entries:
        seen = entry.setdefault('seen', _collections.OrderedDict())
        if dedupe_key in seen:
            continue  # this entry already fired on this row+updated_at
        seen[dedupe_key] = source
        while len(seen) > _DEDUPE_LRU_CAP:
            seen.popitem(last=False)
        fired_any = True
        try:
            entry['handler'](item)
        except Exception as e:
            item_key = item.get('key') if isinstance(item, dict) else '<unknown>'
            log_error(
                f"Handler {coll}.{state} failed on key={item_key!r} "
                f"(source={source}): {e}"
            )

    # Advance shared cursor past this row regardless of handler success.
    # Done once per row (not per entry); subsequent safety-poll queries
    # filter on (updated_at, key) > cursor, so the row never re-surfaces.
    if fired_any:
        sort_key = _row_sort_key(item)
        cursor = _cursors.get(key)
        if cursor is None or sort_key > cursor:
            _cursors[key] = sort_key


def _iso(ts) -> str:
    """ISO-format a timestamp for the dedupe-LRU key. Stable across paths."""
    if ts is None:
        return ''
    if hasattr(ts, 'isoformat'):
        return ts.isoformat()
    return str(ts)


# =============================================================================
# Safety poll / NOTIFY drain / delayed dispatch — three dispatch entry points
# =============================================================================

def _run_safety_poll_if_due():
    """10-second safety poll — catches anything LISTEN missed.

    Per (collection, state) registration: query for rows with
    (updated_at, key) > cursor. If any are found, log_warn and dispatch them
    through the shared dispatch path.
    """
    global _last_safety_poll_ts
    now = time.monotonic()
    if now - _last_safety_poll_ts < _SAFETY_POLL_INTERVAL_SEC:
        return
    _last_safety_poll_ts = now

    if not _handlers:
        return

    provider = _get_provider()
    for (coll_name, state), entries in list(_handlers.items()):
        # Safety poll only catches up the LIVE-dispatch path — delayed
        # handlers run their own per-tick query and manage their own
        # cursor. Skip the (coll, state) if every handler is delayed;
        # otherwise pass the non-delayed entries forward.
        live_entries = [e for e in entries if not (e.get('delay_seconds') or 0.0)]
        if not live_entries:
            continue

        cursor = _cursors.get((coll_name, state))
        if cursor is None:
            # No cursor yet (registration hasn't flushed). Skip — next tick
            # picks it up.
            continue

        rows = provider.fetch_rows_since(coll_name, state, cursor)
        if not rows:
            _safety_poll_seeded.add((coll_name, state))
            continue

        seeded = (coll_name, state) in _safety_poll_seeded
        if not seeded:
            # First tick for this (coll, state) — cursor=epoch by design,
            # so finding rows is the documented startup dispatch path, not
            # a NOTIFY drop. Log it as info, not warn.
            log_info(
                f"safety poll seeded {coll_name}.{state} with {len(rows)} "
                f"existing row(s) on first tick"
            )
            _safety_poll_seeded.add((coll_name, state))
        else:
            log_warn(
                f"safety poll caught {len(rows)} row(s) that LISTEN missed "
                f"for {coll_name}.{state} (cursor={cursor})"
            )

        for item in rows:
            _dispatch_to_entries(live_entries, item, source='poll')


def _run_delayed_dispatch():
    """Per-tick: fire delayed handlers whose rows are now due.

    Delayed handlers do NOT dispatch on NOTIFY. Their entire dispatch path
    is this query, run once per `run_pending()` tick:
        rows where state = watched_state          (strict cancellation)
              AND (updated_at, key) > delay_cursor (no re-fire)
              AND updated_at + delay_seconds <= NOW()  (the delay)
    The handler fires, the per-handler cursor advances, and we move on.
    Granularity is the run_pending() cadence (typically 1s).
    """
    if not _handlers:
        return
    provider = _get_provider()
    for (coll_name, state), entries in list(_handlers.items()):
        for entry in entries:
            delay_seconds = entry.get('delay_seconds') or 0.0
            if delay_seconds <= 0:
                continue
            cursor = entry.get('delay_cursor')
            if cursor is None:
                continue

            rows = provider.fetch_due_rows(coll_name, state, cursor, delay_seconds)
            if not rows:
                continue

            for item in rows:
                # Delayed dispatch path bypasses the shared dedupe LRU — its
                # own cursor advancement is monotonic and prevents re-fire
                # within this lane. The shared LRU is only relevant for the
                # LISTEN-vs-safety-poll race in the live lane.
                try:
                    entry['handler'](item)
                except Exception as e:
                    item_key = item.get('key') if isinstance(item, dict) else '<unknown>'
                    log_error(
                        f"Delayed handler {coll_name}.{state} failed "
                        f"on key={item_key!r} (delay={delay_seconds}s): {e}"
                    )
                # Advance per-entry cursor regardless of handler success.
                sort_key = _row_sort_key(item)
                if sort_key > entry['delay_cursor']:
                    entry['delay_cursor'] = sort_key


def _drain_notifications():
    """Poll the connection for NOTIFYs and dispatch.

    Channel names are opaque hashes — trust the payload, validate against
    `_handlers`. Mismatch = md5 collision OR an orphaned LISTEN; log_error
    once per process-lifetime and drop.
    """
    provider = _get_provider()
    notifications = provider.poll_notifications()
    if not notifications:
        return

    # Group rows by (collection, state) so we can apply process_latest_only
    # to the batch coherently. Multi-NOTIFY drains happen when the agent's
    # main loop has been busy.
    grouped: Dict[tuple, List[dict]] = {}

    for note in notifications:
        channel = note['channel']
        payload = note['payload'] or {}

        collection = payload.get('collection')
        state = payload.get('state_after') or payload.get('state')
        row_key = payload.get('key')
        factory = payload.get('factory_name')

        if not (collection and state and row_key):
            log_error(f"Malformed NOTIFY payload on {channel}: {payload!r}")
            continue

        handler_key = (collection, state)
        entries = _handlers.get(handler_key)
        if not entries:
            # md5 collision OR a stale subscription. Rate-limit the warn so
            # we don't flood logs if a real collision (or misconfigured
            # trigger) is firing repeatedly.
            collision_token = (channel, collection, state)
            if collision_token not in _collision_warned:
                _collision_warned.add(collision_token)
                log_error(
                    f"NOTIFY routed to no handler: channel={channel} "
                    f"payload={collection}.{state} — md5 collision or orphan "
                    f"LISTEN. Logging once per (channel, coll, state) per process."
                )
            continue

        item = provider.fetch_item(factory, collection, row_key)
        if not item:
            continue

        grouped.setdefault(handler_key, []).append(item)

    for handler_key, items in grouped.items():
        entries = _handlers.get(handler_key, [])
        # NOTIFY dispatch fires only the live (non-delayed) handlers. Delayed
        # handlers have their own per-tick query path and skip this lane.
        live_entries = [e for e in entries if not (e.get('delay_seconds') or 0.0)]
        if not live_entries:
            continue
        # Sort to keep dispatch in (updated_at, key) order — matters for
        # multi-row drains where handler ordering may matter.
        batch = sorted(items, key=_row_sort_key)
        for item in batch:
            _dispatch_to_entries(live_entries, item, source='listen')
