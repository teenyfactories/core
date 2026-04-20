"""Base message queue abstraction with provider support"""

import os
import schedule as _schedule
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Dict, Any

from teenyfactories.config import FACTORY_PREFIX
from teenyfactories.logging import log_info, log_error, log_debug, log_warn
from teenyfactories.utils import get_timestamp, generate_unique_id


# =============================================================================
# ABSTRACT BASE CLASS
# =============================================================================

class MessageQueueProvider(ABC):
    """Abstract base class for message queue providers"""

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def send(self, topic: str, payload: dict):
        pass

    @abstractmethod
    def subscribe(self, topics: List[str], options: Optional[Dict] = None):
        pass

    @abstractmethod
    def receive_one(self, timeout: float = 0.1) -> Optional[dict]:
        pass


# =============================================================================
# PROVIDER REGISTRY
# =============================================================================

_provider_instance: Optional[MessageQueueProvider] = None
_topic_handlers: Dict[str, List[dict]] = {}


def _get_provider() -> MessageQueueProvider:
    """Get or create the message queue provider instance (PostgreSQL only)"""
    global _provider_instance

    if _provider_instance is None:
        from .providers.postgres import PostgresProvider
        _provider_instance = PostgresProvider()
        _provider_instance.connect()
        log_info("Connected to PostgreSQL message queue")

    return _provider_instance


def _format_topic_name(topic: str) -> str:
    """Format topic name with factory prefix if configured"""
    if FACTORY_PREFIX:
        return f"{FACTORY_PREFIX}.{topic}"
    return topic


# =============================================================================
# PUBLIC API
# =============================================================================

class MessageSendBuilder:
    """Fluent builder for tf.send_message('topic').with_data({})"""

    def __init__(self, topic: str):
        self._topic = topic

    def with_data(self, payload: dict = None):
        """Send the message with the given payload."""
        return _do_send(self._topic, payload)


def send_message(topic: str):
    """
    Send a message to a topic.

    Usage:
        tf.send_message('data_ready').with_data({'status': 'completed'})
        tf.send_message('ping').with_data()
    """
    return MessageSendBuilder(topic)


def _do_send(topic: str, payload: dict = None):
    """Internal: actually send the message."""
    try:
        provider = _get_provider()
        formatted_topic = _format_topic_name(topic)

        message = {
            'topic': topic,
            'data': payload or {},
            'timestamp': get_timestamp(),
            'id': generate_unique_id()
        }

        provider.send(formatted_topic, message)
        log_info(f"Sent message to topic: {formatted_topic}")
        return True

    except Exception as e:
        log_error(f"Failed to send message to topic {topic}: {e}")
        return False


class MessageSubscriptionBuilder:
    """Fluent builder for tf.on_message('topic').do(handler)

    Usage:
        tf.on_message('topic').do(handler)
        tf.on_message('topic').on_startup_replay_latest().do(handler)
        tf.on_message('topic').process_latest_only().do(handler)
        tf.on_message('topic').on_startup_replay_latest().process_latest_only().do(handler)
    """

    def __init__(self, topic: str):
        self._topic = topic
        self._on_startup_replay_latest = False
        self._process_latest_only = False

    def on_startup_replay_latest(self):
        """Process the most recent existing state when the container starts."""
        self._on_startup_replay_latest = True
        return self

    def process_latest_only(self):
        """Skip intermediate states — only process the newest since last poll."""
        self._process_latest_only = True
        return self

    def do(self, handler: Callable):
        """Register handler for this topic."""
        _register_topic_handler(
            self._topic, handler,
            on_startup_replay_latest=self._on_startup_replay_latest,
            process_latest_only=self._process_latest_only,
        )
        return handler


def on_message(topic: str) -> MessageSubscriptionBuilder:
    """
    Subscribe to a message topic.

    Usage:
        tf.on_message('data_collected').do(handle_data)
        tf.on_message('config').on_startup_replay_latest().do(handle_config)
        tf.on_message('status').process_latest_only().do(handle_status)
    """
    return MessageSubscriptionBuilder(topic)


def _register_topic_handler(topic: str, handler: Callable,
                             on_startup_replay_latest: bool = False,
                             process_latest_only: bool = False):
    """Subscribe via provider and store the handler with its options."""
    global _topic_handlers

    provider = _get_provider()
    formatted_topic = _format_topic_name(topic)

    options = {
        formatted_topic: {
            'on_startup_replay_latest': on_startup_replay_latest,
            'process_latest_only': process_latest_only,
        }
    }
    provider.subscribe([formatted_topic], options)

    if topic not in _topic_handlers:
        _topic_handlers[topic] = []
    _topic_handlers[topic].append({
        'handler': handler,
        'on_startup_replay_latest': on_startup_replay_latest,
        'process_latest_only': process_latest_only,
    })

    log_info(f"Registered handler for topic: {formatted_topic}")


def run_pending(timeout: float = 0.1):
    """
    Run pending scheduled jobs and process one pending message.

    Call in a loop:
        while True:
            tf.run_pending()
            tf.sleep(1)
    """
    # Publish MCP catalog on first tick (deferred so order doesn't matter)
    from teenyfactories.mcp import _maybe_publish_mcp
    _maybe_publish_mcp()

    _schedule.run_pending()
    _run_next_message(timeout)


def _run_next_message(timeout: float = 0.1):
    """Poll factory_states for the next unprocessed state, dispatch to handlers."""
    try:
        provider = _get_provider()
        message = provider.receive_one(timeout=timeout)

        if not message:
            return False

        topic = message.get('topic')

        # Dispatch to topic-specific handlers
        entries = _topic_handlers.get(topic, [])
        for entry in entries:
            try:
                entry['handler'](message)
            except Exception as e:
                log_error(f"Error in handler for topic {topic}: {e}")

        return True

    except Exception as e:
        log_error(f"Error in run_pending: {e}")
        return False
