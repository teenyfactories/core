"""Message queue: LISTEN/NOTIFY-backed pub/sub over factory_data.

Everything flows through `factory_data`. Messages live in the `_messages`
collection (state = topic, key = uuid); lifecycle items live in normal
collections where `state` advances through the pipeline.

Two subscription primitives are exposed to factory code:

    tf.on_message('topic')            — wraps tf.on_state('_messages', 'topic')
    tf.on_state('collection', 'state') — replay-then-listen

Both are driven by the single LISTEN/NOTIFY loop in `run_pending()`.
"""

import schedule as _schedule
from typing import Callable, Optional, Dict, List

from teenyfactories.config import FACTORY_PREFIX
from teenyfactories.logging import log_info, log_error
from teenyfactories.utils import get_timestamp, generate_unique_id


# =============================================================================
# Provider singleton
# =============================================================================

_provider_instance = None

# handler registry keyed by (collection, state)
# value: list of dicts { handler, replay, replayed }
_handlers: Dict[tuple, List[dict]] = {}


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
    """Internal: insert the message row via tf.store()."""
    try:
        # Import lazily to avoid circular import on module init
        from teenyfactories.store import store
        store('_messages').set(
            key=None,                                 # UUID auto-generated
            value=payload or {},
            state=topic,
            user_id='system',
        )
        log_info(f"send_message published to _messages.{topic}")
        return True
    except Exception as e:
        log_error(f"send_message failed for topic {topic}: {e}")
        return False


# =============================================================================
# on_message — thin wrapper over on_state('_messages', topic)
# =============================================================================

class MessageSubscriptionBuilder:
    """Fluent builder for tf.on_message('topic').do(handler).

    Kept for backward compatibility. Internally delegates to on_state.
    The legacy .on_startup_replay_latest() / .process_latest_only() chains
    are accepted but only .on_startup_replay_latest() has meaning now:
    it enables replay of _messages rows still in that state.
    """

    def __init__(self, topic: str):
        self._topic = topic
        self._replay = False
        self._process_latest_only = False  # no-op under the new model

    def on_startup_replay_latest(self):
        self._replay = True
        return self

    def process_latest_only(self):
        # Kept for source-compat; no longer meaningful.
        self._process_latest_only = True
        return self

    def do(self, handler: Callable):
        topic = self._topic

        def wrapper(item):
            # Present a message-shaped dict to legacy handlers.
            handler({
                'topic':     item['state'],
                'data':      item.get('value') or {},
                'id':        item['key'],
                'key':       item['key'],
                'timestamp': item.get('created_at'),
            })

        _register_subscription(
            collection='_messages',
            state=topic,
            handler=wrapper,
            replay=self._replay,
        )
        return handler


def on_message(topic: str) -> MessageSubscriptionBuilder:
    """Subscribe to a fire-and-forget message topic."""
    return MessageSubscriptionBuilder(topic)


# =============================================================================
# on_state — new primary API
# =============================================================================

class StateSubscriptionBuilder:
    """Fluent builder for tf.on_state(collection, state).do(handler)."""

    def __init__(self, collection: str, state: str):
        self._collection = collection
        self._state = state
        self._replay = True  # default ON — "inbox" semantics

    def no_replay(self):
        """Disable startup replay of existing rows already in this state."""
        self._replay = False
        return self

    def do(self, handler: Callable):
        _register_subscription(
            collection=self._collection,
            state=self._state,
            handler=handler,
            replay=self._replay,
        )
        return handler


def on_state(collection: str, state: str) -> StateSubscriptionBuilder:
    """
    Subscribe to state transitions on a collection.

    Usage:
        @tf.on_state('documents', 'loaded').do
        def handle_loaded(item):
            # item = {factory_name, collection, key, value, state, user_id,
            #         created_at, updated_at}
            ...

    On subscription, the handler is fired once for every existing row already
    in (collection, state) — this gives workers an 'inbox' on restart so they
    never miss queued work. Call .no_replay() to opt out.
    """
    return StateSubscriptionBuilder(collection, state)


# =============================================================================
# Registration + LISTEN
# =============================================================================

def _register_subscription(collection: str, state: str, handler: Callable, replay: bool):
    provider = _get_provider()
    factory_name = FACTORY_PREFIX

    channel = f"{factory_name}.{collection}.{state}"
    if len(channel) > 63:
        raise ValueError(
            f"NOTIFY channel name too long ({len(channel)} > 63): {channel!r}"
        )

    provider.listen(channel)

    key = (collection, state)
    _handlers.setdefault(key, []).append({
        'handler': handler,
        'replay':  replay,
        'replayed': False,
    })

    log_info(f"Registered handler for {collection}.{state} (replay={replay})")


# =============================================================================
# Event loop: run_pending + dispatch
# =============================================================================

def run_pending(timeout: float = 0.1):
    """Run scheduled jobs, replay any pending subscriptions, dispatch notifications.

    Factories call this in a loop:
        while True:
            tf.run_pending()
            tf.sleep(1)
    """
    # Publish MCP catalog on first tick (deferred so order doesn't matter)
    from teenyfactories.mcp import _maybe_publish_mcp
    _maybe_publish_mcp()

    _schedule.run_pending()
    _replay_pending_subscriptions()
    _drain_notifications()


def _replay_pending_subscriptions():
    """Fire handlers for all existing rows already in (collection, state)."""
    from teenyfactories.store import store
    for (collection, state), entries in _handlers.items():
        for entry in entries:
            if entry['replayed'] or not entry['replay']:
                entry['replayed'] = True
                continue
            try:
                items = store(collection).find_items(state=state)
                for item in items:
                    try:
                        entry['handler'](item)
                    except Exception as e:
                        log_error(f"Error in replay handler for {collection}.{state}: {e}")
            except Exception as e:
                log_error(f"Replay query failed for {collection}.{state}: {e}")
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

        # Hydrate the row so handlers get the full item
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
                log_error(f"Handler for {collection}.{state} failed: {e}")
