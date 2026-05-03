"""Message queue: LISTEN/NOTIFY-backed pub/sub over factory_data.

Everything flows through `factory_data`. Messages live in the `_messages`
collection (state = topic, key = uuid); lifecycle items live in normal
collections where `state` advances through the pipeline.

Two subscription primitives are exposed to factory code:

    tf.on_state('collection', 'state')   — primary primitive
    tf.on_message('topic')               — pure shorthand for
                                           tf.on_state('_messages', 'topic')

Both return the same builder. Default behaviour: listen for new rows only.
Opt into startup replay with `.on_startup_replay_latest()`; further restrict
replay to only the most-recent row with `.process_latest_only()`.

Both are driven by the single LISTEN/NOTIFY loop in `run_pending()`.

Lifecycle:
    1. Factory module imports tf and decorates handlers with `@tf.on_state(...)
       .do(...)`. Registrations are QUEUED — no DB connection opens at import
       time.
    2. Factory calls `tf.run_pending()` for the first time. The loop:
         a. Runs `_first_tick_init()` once: drains the pending-registrations
            queue (opens connection, issues LISTEN per channel) and publishes
            the MCP catalog.
         b. Runs scheduled jobs.
         c. Replays existing rows for any new (collection, state) handlers.
         d. Drains LISTEN/NOTIFY queue and dispatches to handlers.
    3. Subsequent calls to `run_pending()` skip step 2a (idempotent) but pick
       up any registrations made AFTER the first tick (e.g. a handler that
       calls `tf.on_state(...)` re-entrantly) — those are flushed at the
       start of step 2d, so dispatch never sees a half-registered handler.
"""

import schedule as _schedule
from typing import Callable, Dict, List

from teenyfactories.config import FACTORY_NAME
from teenyfactories.logging import log_info, log_error


# =============================================================================
# Provider singleton + handler registry
# =============================================================================

_provider_instance = None

# Active handler registry, keyed by (collection, state).
# Each value is a list of dicts: { handler, replay, replayed }.
_handlers: Dict[tuple, List[dict]] = {}

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

    Default: listen for new NOTIFY events only — no startup replay.
    Chain methods:
        .on_startup_replay_latest()  — replay rows already in (collection, state) at startup
        .process_latest_only()       — when replaying, only fire the handler on
                                       the single most-recent row, not every row
    """

    def __init__(self, collection: str, state: str):
        self._collection = collection
        self._state = state
        self._replay = False
        self._latest_only = False

    def on_startup_replay_latest(self):
        self._replay = True
        return self

    def process_latest_only(self):
        self._latest_only = True
        return self

    def do(self, handler: Callable):
        _enqueue_registration(
            collection=self._collection,
            state=self._state,
            handler=handler,
            replay=self._replay,
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
    startup replay of already-present rows with `.on_startup_replay_latest()`.
    Combine with `.process_latest_only()` to replay just the most-recent row.
    """
    return SubscriptionBuilder(collection, state)


def on_message(topic: str) -> SubscriptionBuilder:
    """Pure shorthand for `tf.on_state('_messages', topic)`."""
    return on_state('_messages', topic)


# =============================================================================
# Registration queue — deferred LISTEN avoids cursor races
# =============================================================================

def _enqueue_registration(collection: str, state: str, handler: Callable,
                          replay: bool, latest_only: bool = False):
    """
    Queue a subscription registration. The actual LISTEN + handler-table
    insertion happens later, when `_flush_registrations()` is called from
    inside the run_pending lifecycle. Defers all DB I/O off the import path
    and isolates LISTEN from the connection's poll cursor.
    """
    channel = f"{FACTORY_NAME}.{collection}.{state}"
    if len(channel) > 63:
        raise ValueError(
            f"NOTIFY channel name too long ({len(channel)} > 63): {channel!r}"
        )
    _pending_registrations.append({
        'collection':  collection,
        'state':       state,
        'handler':     handler,
        'replay':      replay,
        'latest_only': latest_only,
        'channel':     channel,
    })


def _flush_registrations():
    """
    Drain the pending-registrations queue: open the provider connection if
    needed, issue LISTEN once per distinct channel, and add each handler to
    the active `_handlers` registry. Idempotent — safe to call repeatedly.
    """
    if not _pending_registrations:
        return

    provider = _get_provider()
    listened: set = set()  # avoid duplicate LISTEN within one flush

    while _pending_registrations:
        reg = _pending_registrations.pop(0)
        channel = reg['channel']
        if channel not in listened:
            try:
                provider.listen(channel)
                listened.add(channel)
            except Exception as e:
                log_error(f"LISTEN {channel} failed: {e}")
                continue

        key = (reg['collection'], reg['state'])
        _handlers.setdefault(key, []).append({
            'handler':     reg['handler'],
            'replay':      reg['replay'],
            'latest_only': reg.get('latest_only', False),
            'replayed':    False,
        })
        log_info(
            f"Registered handler for {reg['collection']}.{reg['state']} "
            f"(replay={reg['replay']}, latest_only={reg.get('latest_only', False)})"
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
    """Drain scheduled jobs, replay pending subscriptions, dispatch notifications.

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

    _schedule.run_pending()
    _replay_pending_subscriptions()
    _drain_notifications()


def _replay_pending_subscriptions():
    """Fire handlers for existing rows already in (collection, state).

    Honours per-entry flags:
      - replay=False         → skip replay entirely (still subscribes via LISTEN).
      - latest_only=True     → fire only on the single most-recent row, ordered
                               by created_at DESC.
    """
    from teenyfactories.collection import collection as _coll
    for (coll_name, state), entries in _handlers.items():
        for entry in entries:
            if entry['replayed'] or not entry['replay']:
                entry['replayed'] = True
                continue
            try:
                items = _coll(coll_name).get_all(state=state)
                if entry.get('latest_only') and items:
                    items = [max(items, key=lambda r: r.get('created_at') or '')]
                for item in items:
                    try:
                        entry['handler'](item)
                    except Exception as e:
                        # Include item key in the log so the bad row is
                        # findable via factory_data without guessing.
                        item_key = item.get('key') if isinstance(item, dict) else '<unknown>'
                        log_error(
                            f"Replay handler {coll_name}.{state} failed "
                            f"on key={item_key!r}: {e}"
                        )
            except Exception as e:
                log_error(f"Replay query failed for {coll_name}.{state}: {e}")
            entry['replayed'] = True


def _drain_notifications():
    """Poll the connection for notifications and dispatch."""
    provider = _get_provider()
    notifications = provider.poll_notifications()
    if not notifications:
        return

    for note in notifications:
        channel = note['channel']
        payload = note['payload'] or {}

        # Parse channel: {factory}.{collection}.{state}
        parts = channel.split('.', 2)
        if len(parts) < 3:
            continue
        _factory, collection, state = parts

        # Skip the _changed channel — that's for UI refresh only, not Python handlers.
        if state == '_changed':
            continue

        key = (collection, state)
        entries = _handlers.get(key, [])
        if not entries:
            continue

        # Hydrate the row so handlers get the full item.
        item = provider.fetch_item(
            factory_name=payload.get('factory_name'),
            collection=payload.get('collection', collection),
            key=payload.get('key'),
        )
        if not item:
            continue

        for entry in entries:
            try:
                entry['handler'](item)
            except Exception as e:
                item_key = item.get('key') if isinstance(item, dict) else '<unknown>'
                log_error(
                    f"Handler {collection}.{state} failed on key={item_key!r}: {e}"
                )
