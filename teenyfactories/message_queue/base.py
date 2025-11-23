"""Base message queue abstraction with provider support"""

import os
import threading
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Dict, Any

# Import schedule library for recurring tasks
try:
    import schedule
except ImportError:
    schedule = None

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
        """Establish connection to the message queue"""
        pass

    @abstractmethod
    def send(self, topic: str, payload: dict):
        """
        Send a message to a topic

        Args:
            topic: Topic name (already prefixed)
            payload: Complete message payload with metadata
        """
        pass

    @abstractmethod
    def subscribe(self, topics: List[str]):
        """
        Subscribe to topics

        Args:
            topics: List of topic names (already prefixed)
        """
        pass

    @abstractmethod
    def receive_one(self, timeout: float = 0.1) -> Optional[dict]:
        """
        Receive a single message (non-blocking with timeout)

        Args:
            timeout: Maximum time to wait for a message in seconds

        Returns:
            Message dict or None if no message available
        """
        pass

    @abstractmethod
    def set_key(self, key: str, value: str, expiration: Optional[int] = None):
        """
        Set a key-value pair

        Args:
            key: Key name
            value: Value to store
            expiration: Optional expiration time in seconds
        """
        pass

    @abstractmethod
    def get_key(self, key: str) -> Optional[str]:
        """
        Get a value by key

        Args:
            key: Key name

        Returns:
            Value or None if key doesn't exist
        """
        pass

    @abstractmethod
    def delete_key(self, key: str):
        """
        Delete a key

        Args:
            key: Key name
        """
        pass


# =============================================================================
# PROVIDER REGISTRY
# =============================================================================

_provider_instance: Optional[MessageQueueProvider] = None
_message_callbacks: List[Callable] = []
_subscribed_topics: List[str] = []
_listener_thread: Optional[threading.Thread] = None
_listener_running: bool = False


def _get_provider() -> MessageQueueProvider:
    """Get or create the message queue provider instance"""
    global _provider_instance

    if _provider_instance is None:
        provider_name = os.getenv('MESSAGE_QUEUE_PROVIDER', 'redis')

        if provider_name == 'redis':
            from .providers.redis import RedisProvider
            _provider_instance = RedisProvider()
        elif provider_name == 'postgres':
            from .providers.postgres import PostgresProvider
            _provider_instance = PostgresProvider()
        else:
            raise ValueError(f"Unsupported message queue provider: {provider_name}")

        # Connect to the provider
        _provider_instance.connect()
        log_info(f"🔌 Connected to message queue provider: {provider_name}")

    return _provider_instance


def _format_topic_name(topic: str) -> str:
    """Format topic name with factory prefix if configured"""
    if FACTORY_PREFIX:
        return f"{FACTORY_PREFIX}.{topic}"
    return topic


def _background_listener():
    """Background thread that listens for messages and dispatches to callbacks"""
    global _listener_running

    provider = _get_provider()
    log_info("🎧 Background message listener started")

    while _listener_running:
        try:
            # Receive one message with short timeout
            message = provider.receive_one(timeout=0.1)

            if message:
                topic = message.get('topic')

                # Call all registered callbacks
                for callback in _message_callbacks:
                    try:
                        callback(message)
                    except Exception as e:
                        log_error(f"❌ Error in message callback for topic {topic}: {e}")

        except Exception as e:
            log_error(f"❌ Error in background listener: {e}")

    log_info("🛑 Background message listener stopped")


# =============================================================================
# PUBLIC API FUNCTIONS
# =============================================================================

def send_message(topic: str, payload: dict = None):
    """
    Send a message to a topic

    Args:
        topic: Topic name (without prefix)
        payload: Message payload data

    Returns:
        bool: True if successful, False otherwise

    Example:
        >>> send_message('data_ready', {'dataset_id': '123', 'status': 'completed'})
    """
    try:
        provider = _get_provider()

        # Format topic with factory prefix
        formatted_topic = _format_topic_name(topic)

        # Build complete message with metadata
        message = {
            'topic': topic,
            'data': payload or {},
            'timestamp': get_timestamp(),
            'id': generate_unique_id()
        }

        provider.send(formatted_topic, message)
        log_info(f"📢 Sent message to topic: {formatted_topic}")
        return True

    except Exception as e:
        log_error(f"❌ Failed to send message to topic {topic}: {e}")
        return False


def subscribe_to_message(callback: Callable, topics: List[str] = None):
    """
    Subscribe to messages on specific topics

    Args:
        callback: Function to call when a message is received
                  Signature: callback(message: dict)
        topics: List of topic names to subscribe to (without prefix)
                If None, subscribes to all topics

    Example:
        >>> def handle_message(message):
        ...     print(f"Received: {message}")
        >>> subscribe_to_message(handle_message, topics=['data_ready', 'task_complete'])
    """
    global _message_callbacks, _subscribed_topics, _listener_thread, _listener_running

    try:
        provider = _get_provider()

        # Register callback
        _message_callbacks.append(callback)

        # Format topics with prefix
        if topics:
            formatted_topics = [_format_topic_name(topic) for topic in topics]
            log_info(f"🎧 Registering subscription to topics: {formatted_topics}")
        else:
            formatted_topics = None
            log_info("🎧 Registering subscription to all topics")

        # Subscribe via provider
        provider.subscribe(formatted_topics or [])
        _subscribed_topics = formatted_topics or []

        # Start background listener thread if not already running
        if not _listener_running:
            _listener_running = True
            _listener_thread = threading.Thread(target=_background_listener, daemon=True)
            _listener_thread.start()

    except Exception as e:
        log_error(f"❌ Failed to subscribe to messages: {e}")


def schedule_task(task_func: Callable, interval_seconds: int):
    """
    Schedule a recurring task

    Args:
        task_func: Function to call on schedule
        interval_seconds: Interval in seconds between calls

    Example:
        >>> def check_status():
        ...     print("Checking status...")
        >>> schedule_task(check_status, interval_seconds=60)
    """
    if schedule is None:
        log_error("❌ schedule library not available - install with 'pip install schedule'")
        return

    try:
        schedule.every(interval_seconds).seconds.do(task_func)
        log_info(f"⏰ Scheduled task to run every {interval_seconds} seconds")
    except Exception as e:
        log_error(f"❌ Failed to schedule task: {e}")


def wait_for_next_message_or_scheduled_task():
    """
    Unified main loop that waits for messages and runs scheduled tasks

    This is a blocking call that should be used as the main event loop.
    It will:
    1. Process any incoming messages via registered callbacks
    2. Run any pending scheduled tasks
    3. Sleep briefly to prevent high CPU usage

    Example:
        >>> def handle_message(msg):
        ...     print(f"Got: {msg}")
        >>> def periodic_check():
        ...     print("Running check...")
        >>> subscribe_to_message(handle_message, ['events'])
        >>> schedule_task(periodic_check, 60)
        >>> wait_for_next_message_or_scheduled_task()
    """
    import time

    if schedule is None:
        log_error("❌ schedule library not available - install with 'pip install schedule'")
        return

    try:
        log_info("🔄 Starting main event loop (messages + scheduled tasks)")

        while True:
            # Run pending scheduled jobs
            schedule.run_pending()

            # Sleep to prevent high CPU usage
            # (messages are handled by background listener thread)
            time.sleep(1)

    except KeyboardInterrupt:
        log_info("🛑 Main event loop interrupted")
    except Exception as e:
        log_error(f"❌ Error in main event loop: {e}")


# =============================================================================
# COORDINATION HELPER FUNCTIONS
# =============================================================================

def publish_service_status(service_name: str, status: str, details: Dict[str, Any] = None, service_type: str = 'auto'):
    """
    Publish service status update

    Args:
        service_name: Name of the service (e.g., 'data_profiler', 'script_executor')
        status: Status string (e.g., 'running', 'completed', 'failed', 'error')
        details: Additional status details
        service_type: 'agent', 'worker', or 'auto' (auto-detects from service name)

    Example:
        >>> publish_service_status('data_profiler', 'running', {'progress': 0.5})
    """
    try:
        # Auto-detect service type if not specified
        if service_type == 'auto':
            if any(agent_type in service_name for agent_type in ['agent', 'profiler', 'planner', 'analyst', 'assistant']):
                service_type = 'agent'
            elif any(worker_type in service_name for worker_type in ['worker', 'executor', 'engine', 'interpreter']):
                service_type = 'worker'
            else:
                service_type = 'service'  # fallback

        # Get container name from environment or derive it
        container_name = os.getenv('HOSTNAME', f"{service_type}_{service_name}")

        event_data = {
            'service_name': service_name,
            'service_type': service_type,
            'container_name': container_name,
            'status': status,
            'timestamp': get_timestamp(),
            **(details or {})
        }

        send_message('service_status', event_data)
        log_debug(f"⚙️ Published service status: {service_name} ({service_type}) -> {status}")
        return True

    except Exception as e:
        log_error(f"❌ Error publishing service status for {service_name}: {e}")
        return False


def set_agent_ready(agent_name: str):
    """
    Mark an agent as ready

    Args:
        agent_name: Name of the agent

    Example:
        >>> set_agent_ready('data_profiler')
    """
    try:
        provider = _get_provider()
        provider.set_key(f"agent_ready:{agent_name}", "true", expiration=3600)  # Expires in 1 hour
        send_message('agent_ready', {'agent': agent_name})
        log_info(f"✅ Agent marked as ready: {agent_name}")
    except Exception as e:
        log_error(f"❌ Failed to mark agent ready {agent_name}: {e}")


def is_agent_ready(agent_name: str) -> bool:
    """
    Check if an agent is ready

    Args:
        agent_name: Name of the agent

    Returns:
        bool: True if agent is ready, False otherwise

    Example:
        >>> if is_agent_ready('data_profiler'):
        ...     print("Agent is ready!")
    """
    try:
        provider = _get_provider()
        value = provider.get_key(f"agent_ready:{agent_name}")
        return bool(value)
    except Exception as e:
        log_error(f"❌ Failed to check agent ready status {agent_name}: {e}")
        return False


def acquire_processing_lock(agent_name: str, event_id: str, timeout: int = 300) -> bool:
    """
    Acquire a processing lock for an event to prevent duplicate processing

    Args:
        agent_name: Name of the agent
        event_id: ID of the event being processed
        timeout: Lock expiration time in seconds

    Returns:
        bool: True if lock acquired, False if already locked

    Example:
        >>> if acquire_processing_lock('data_profiler', event['id']):
        ...     # Process the event
        ...     release_processing_lock('data_profiler', event['id'])
    """
    try:
        provider = _get_provider()
        lock_key = f"processing_lock:{agent_name}:{event_id}"

        # Try to set the key only if it doesn't exist
        existing = provider.get_key(lock_key)
        if existing:
            log_info(f"⏭️ Event already being processed: {agent_name} for event {event_id[:8]}...")
            return False

        provider.set_key(lock_key, "locked", expiration=timeout)
        log_info(f"🔒 Acquired processing lock: {agent_name} for event {event_id[:8]}...")
        return True

    except Exception as e:
        log_error(f"❌ Failed to acquire processing lock {agent_name}: {e}")
        return False


def release_processing_lock(agent_name: str, event_id: str):
    """
    Release a processing lock

    Args:
        agent_name: Name of the agent
        event_id: ID of the event

    Example:
        >>> release_processing_lock('data_profiler', event['id'])
    """
    try:
        provider = _get_provider()
        lock_key = f"processing_lock:{agent_name}:{event_id}"
        provider.delete_key(lock_key)
        log_info(f"🔓 Released processing lock: {agent_name} for event {event_id[:8]}...")
    except Exception as e:
        log_error(f"❌ Failed to release processing lock {agent_name}: {e}")
