"""Message queue: LISTEN/NOTIFY-backed pub/sub over factory_data.

Everything flows through `factory_data`. Messages live in the `_messages`
collection (state = topic, key = uuid); lifecycle items live in normal
collections where `state` advances through the pipeline.

Two subscription primitives are exposed to factory code:

    tf.on_state('collection', 'state')   — primary primitive
    tf.on_message('topic')               — pure shorthand for
                                           tf.on_state('_messages', 'topic')

Both return the same builder. Three independent flags shape behaviour:

    .on_startup_replay_latest()  — at startup, fire once on the single
                                   most-recent existing row in (collection,
                                   state). Then live.
    .on_startup_replay_all()     — at startup, fire on every existing row in
                                   (collection, state). Then live.
    .process_latest_only()       — at every dispatch batch (startup replay,
                                   safety poll, multi-NOTIFY drain), compress
                                   the batch to its single most-recent row
                                   (by updated_at, key). Older rows in the
                                   batch are dropped — cursor still advances
                                   so they never fire later.

Combinations are valid; flags compose. Default (none of the above): listen
for new rows only, fire on every NOTIFY.

Transport is a hybrid:
  * LISTEN on hashed channel `tf_state_<md5(factory.collection.state)>` —
    primary, low-latency path.
  * 10s safety poll over (updated_at, key) cursor — catches anything LISTEN
    missed. A non-zero result emits log_warn so operators see NOTIFY drops.
  * Per-(collection, state) dedupe LRU prevents the same row firing twice
    when the safety poll and LISTEN both pick it up in the same window.

Lifecycle:
    1. Factory module imports tf and decorates handlers with `@tf.on_state(...)
       .do(...)`. Registrations are QUEUED — no DB connection opens at import
       time.
    2. Factory calls `tf.run_pending()` for the first time. The loop:
         a. Runs `_first_tick_init()` once: drains the pending-registrations
            queue (opens connection, issues LISTEN per channel) and publishes
            the MCP catalog. Cursor inits per the replay-flag table below.
         b. Runs scheduled jobs.
         c. Replays existing rows for any new (collection, state) handlers
            (honouring the replay flags).
         d. Runs the safety poll if 10s have elapsed since the last one.
         e. Drains LISTEN/NOTIFY queue and dispatches to handlers.
    3. Subsequent calls to `run_pending()` skip step 2a (idempotent) but pick
       up any registrations made AFTER the first tick (e.g. a handler that
       calls `tf.on_state(...)` re-entrantly) — those are flushed at the
       start of step 2e, so dispatch never sees a half-registered handler.

Cursor-init policy at LISTEN-flush time:
    no replay flag           → cursor = now()    (safety poll never resurrects
                                                  rows that existed before the
                                                  agent started — preserves
                                                  the "new rows only" contract)
    .on_startup_replay_latest() → cursor = epoch (replay's single SELECT
                                                  advances cursor to that row)
    .on_startup_replay_all()    → cursor = epoch (replay walks every row,
                                                  cursor advances during
                                                  dispatch)
    For a given (collection, state), the FIRST registration's flags decide
    cursor init. Subsequent registrations on the same key inherit the cursor.
"""

import collections as _collections
import hashlib
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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

# Per-(collection, state) recently-dispatched LRU. Prevents double-fire when
# safety poll and LISTEN both deliver the same row. Key: (row_key,
# updated_at_iso). FIFO-evict at _DEDUPE_LRU_CAP. The dedupe key uses
# updated_at so legitimate re-writes to the same row key (which bump
# updated_at) DO re-fire.
_recent: Dict[tuple, "_collections.OrderedDict"] = {}
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


# Sentinel used as the "epoch" cursor for replay-mode registrations. Strings
# compare lexically with timestamps in `(updated_at, key) > cursor` queries
# in postgres; we pass an actual datetime when calling fetch_rows_since.
from datetime import datetime as _datetime, timezone as _timezone
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

    Default: listen for new NOTIFY events only — no startup replay,
    every row fires the handler once.

    Three independent flags:
        .on_startup_replay_latest() — at startup, fire ONCE on the single
                                      most-recent existing row in
                                      (collection, state).
        .on_startup_replay_all()    — at startup, fire on EVERY existing row
                                      in (collection, state).
        .process_latest_only()      — at every dispatch batch (startup
                                      replay, safety poll, multi-NOTIFY
                                      drain), compress the batch to its
                                      single most-recent row.

    The two startup-replay methods are mutually exclusive — calling both is
    valid; the LAST call wins. `process_latest_only()` composes orthogonally
    with either or neither.
    """

    def __init__(self, collection: str, state: str):
        self._collection = collection
        self._state = state
        self._replay_mode: Optional[str] = None  # None | 'latest' | 'all'
        self._latest_only = False

    def on_startup_replay_latest(self):
        """At startup, fire the handler on only the most-recent existing row."""
        self._replay_mode = 'latest'
        return self

    def on_startup_replay_all(self):
        """At startup, fire the handler on every existing row in (collection, state)."""
        self._replay_mode = 'all'
        return self

    def process_latest_only(self):
        """Compress every dispatch batch (replay + safety-poll + NOTIFY drain) to the single most-recent row."""
        self._latest_only = True
        return self

    def do(self, handler: Callable):
        _enqueue_registration(
            collection=self._collection,
            state=self._state,
            handler=handler,
            replay_mode=self._replay_mode,
            latest_only=self._latest_only,
        )
        return handler


def on_state(collection: str, state: str) -> SubscriptionBuilder:
    """
    Subscribe to state transitions on a collection.

    Usage:
        @tf.on_state('documents', 'loaded').do
        def handle_loaded(item):
            # item = {factory_name, collection, key, user_id, data, state,
            #         created_at, updated_at}
            ...

    Default: only fires on rows that arrive AFTER subscription. Opt into
    startup replay with `.on_startup_replay_latest()` (single row) or
    `.on_startup_replay_all()` (every existing row). Add
    `.process_latest_only()` to compress every dispatch batch to its
    most-recent row.
    """
    return SubscriptionBuilder(collection, state)


def on_message(topic: str) -> SubscriptionBuilder:
    """Pure shorthand for `tf.on_state('_messages', topic)`."""
    return on_state('_messages', topic)


# =============================================================================
# Registration queue — deferred LISTEN avoids cursor races
# =============================================================================

def _enqueue_registration(collection: str, state: str, handler: Callable,
                          replay_mode: Optional[str] = None,
                          latest_only: bool = False):
    """
    Queue a subscription registration. The actual LISTEN + handler-table
    insertion happens later, when `_flush_registrations()` is called from
    inside the run_pending lifecycle. Defers all DB I/O off the import path
    and isolates LISTEN from the connection's poll cursor.

    Channel name is computed at flush time (needs the provider import),
    not here — keeps imports light.
    """
    _pending_registrations.append({
        'collection':  collection,
        'state':       state,
        'handler':     handler,
        'replay_mode': replay_mode,
        'latest_only': latest_only,
    })


def _flush_registrations():
    """
    Drain the pending-registrations queue: open the provider connection if
    needed, issue LISTEN once per distinct hashed channel, add each handler
    to the active `_handlers` registry, and init the per-(collection,state)
    cursor for any newly-seen key. Idempotent.
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

        # Cursor init — first registration for this (collection, state) wins.
        # Subsequent registrations on the same key inherit the existing cursor;
        # otherwise an `_all()` registered after a `_latest()` one would
        # silently rewind the safety-poll window.
        if key not in _cursors:
            if reg['replay_mode'] in ('latest', 'all'):
                # Cursor at epoch — replay's SELECT will advance it.
                _cursors[key] = (_EPOCH, '')
            else:
                # No replay — pin cursor at LISTEN-issue moment so the safety
                # poll doesn't resurrect rows that existed before the agent
                # started.
                _cursors[key] = (_datetime.now(_timezone.utc), '')

        _handlers.setdefault(key, []).append({
            'handler':     reg['handler'],
            'replay_mode': reg['replay_mode'],
            'latest_only': reg.get('latest_only', False),
            'replayed':    False,
        })
        log_info(
            f"Registered handler for {coll}.{state} "
            f"(replay_mode={reg['replay_mode']}, latest_only={reg.get('latest_only', False)})"
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

    _flush_registrations()

    # Publish the MCP catalog once. Lazy import to avoid module-load cycle
    # (mcp imports message_queue for the listen channel registration).
    try:
        from teenyfactories.mcp import _maybe_publish_mcp
        _maybe_publish_mcp()
    except Exception as e:
        log_error(f"MCP catalog publish failed (continuing): {e}")

    _initialized = True


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
        _replay_pending_subscriptions()
    except Exception as e:
        log_error(f"Subscription replay raised: {e}\n{_tb.format_exc()}")
    try:
        _run_safety_poll_if_due()
    except Exception as e:
        log_error(f"Safety poll raised: {e}\n{_tb.format_exc()}")
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


def _compress_to_latest(rows: List[dict]) -> List[dict]:
    """Reduce a batch to its single most-recent row by (updated_at, key).

    Used when any handler in the (collection, state) group has
    `latest_only=True`. The cursor still advances past the dropped rows so
    they never re-fire — that's the documented semantics.
    """
    if not rows:
        return rows
    return [max(rows, key=_row_sort_key)]


def _dispatch_to_entries(entries: List[dict], item: dict, source: str):
    """Fire the handlers for one row, honouring dedupe + advancing cursor.

    `source` is 'replay' | 'listen' | 'poll' — used in failure logs only.
    """
    coll = item.get('collection')
    state = item.get('state')
    if coll is None or state is None:
        return
    key = (coll, state)

    dedupe_key = (item.get('key') or '', _iso(item.get('updated_at')))
    seen = _recent.setdefault(key, _collections.OrderedDict())
    if dedupe_key in seen:
        return  # already dispatched via the other path
    seen[dedupe_key] = source
    while len(seen) > _DEDUPE_LRU_CAP:
        seen.popitem(last=False)

    for entry in entries:
        try:
            entry['handler'](item)
        except Exception as e:
            item_key = item.get('key') if isinstance(item, dict) else '<unknown>'
            log_error(
                f"Handler {coll}.{state} failed on key={item_key!r} "
                f"(source={source}): {e}"
            )

    # Advance cursor past this row regardless of handler success — handler
    # failures are logged; we don't want a poisoned row to be retried forever.
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
# Replay / safety poll / NOTIFY drain — three dispatch entry points
# =============================================================================

def _replay_pending_subscriptions():
    """One-shot: fire handlers for existing rows in (collection, state).

    Honours per-entry flags. `replay_mode` decides which rows are pulled;
    `latest_only` further compresses the resulting batch.
        replay_mode=None     → skip (handler still subscribes via LISTEN).
        replay_mode='latest' → fetch all rows in (coll, state), then keep
                               only the most-recent one.
        replay_mode='all'    → fetch all rows in (coll, state); fire on each.
    """
    from teenyfactories.collection import collection as _coll
    for (coll_name, state), entries in list(_handlers.items()):
        # Decide what work to do for this key. The "replayed" flag is
        # per-entry so a handler added later can still replay even if its
        # siblings already did.
        pending_entries = [e for e in entries if not e['replayed'] and e.get('replay_mode')]
        if not pending_entries:
            for e in entries:
                e['replayed'] = True
            continue

        try:
            rows = _coll(coll_name).get_all(state=state)
        except Exception as e:
            log_error(f"Replay query failed for {coll_name}.{state}: {e}")
            for entry in pending_entries:
                entry['replayed'] = True
            continue

        # Sort once — _dispatch_to_entries uses (updated_at, key) for cursor
        # ordering, so rows arrive in chronological order.
        rows = sorted(rows, key=_row_sort_key)

        # Per-entry replay batch — different handlers on the same (coll, state)
        # may have different replay shapes (rare, but supported by the handler
        # registry). Build the per-entry row list, then dispatch through the
        # shared path so dedupe + cursor advance fire once.
        for entry in pending_entries:
            mode = entry['replay_mode']
            if mode == 'latest':
                batch = _compress_to_latest(rows)
            else:  # 'all'
                batch = list(rows)
            if entry.get('latest_only'):
                batch = _compress_to_latest(batch)
            for item in batch:
                _dispatch_to_entries([entry], item, source='replay')
            entry['replayed'] = True


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
        cursor = _cursors.get((coll_name, state))
        if cursor is None:
            # No cursor yet (registration hasn't flushed). Skip — next tick
            # picks it up.
            continue

        rows = provider.fetch_rows_since(coll_name, state, cursor)
        if not rows:
            continue

        log_warn(
            f"safety poll caught {len(rows)} row(s) that LISTEN missed "
            f"for {coll_name}.{state} (cursor={cursor})"
        )

        any_latest_only = any(e.get('latest_only') for e in entries)
        batch = _compress_to_latest(rows) if any_latest_only else rows

        for item in batch:
            _dispatch_to_entries(entries, item, source='poll')


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
        if not entries:
            continue
        any_latest_only = any(e.get('latest_only') for e in entries)
        batch = _compress_to_latest(items) if any_latest_only else items
        # Sort to keep dispatch in (updated_at, key) order — matters for
        # multi-row drains where handler ordering may matter.
        batch = sorted(batch, key=_row_sort_key)
        for item in batch:
            _dispatch_to_entries(entries, item, source='listen')
