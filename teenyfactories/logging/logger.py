"""Logging implementation for teenyfactories"""

import logging

# Get logger instance (configured in config module)
logger = logging.getLogger('teenyfactories')


def log(message, level='debug'):
    """
    Log message at specified level

    Args:
        message: Message to log
        level: Log level - 'debug', 'info', 'warn', or 'error' (default: 'debug')

    Example:
        >>> log("Processing started", level='info')
        >>> log("Debug details")  # defaults to debug
    """
    level = level.lower()
    if level == 'debug':
        logger.debug(message)
    elif level == 'info':
        logger.info(message)
    elif level in ('warn', 'warning'):
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    else:
        # Fallback for unknown levels
        logger.debug(f"[{level.upper()}] {message}")


def log_debug(message):
    """
    Log debug message (deprecated: use log(message, level='debug'))

    Args:
        message: Message to log
    """
    log(message, level='debug')


def log_info(message):
    """
    Log info message (deprecated: use log(message, level='info'))

    Args:
        message: Message to log
    """
    log(message, level='info')


def log_warn(message):
    """
    Log warning message (deprecated: use log(message, level='warn'))

    Args:
        message: Message to log
    """
    log(message, level='warn')


def log_error(message):
    """
    Log error message (deprecated: use log(message, level='error'))

    Args:
        message: Message to log
    """
    log(message, level='error')


def log_persona(message, level='info', metadata=None):
    """
    Log a first-person message for UI speech bubbles.

    This logs the message normally AND publishes it to the persona_log topic
    for real-time display in the UI as speech bubbles above agent nodes.

    Args:
        message: First-person message (e.g., "I'm processing the data now...")
        level: Log level - 'debug', 'info', 'warn', or 'error' (default: 'info')
        metadata: Optional dict with additional context

    Example:
        >>> log_persona("I found 5 new records to process", level='info')
        >>> log_persona("I'm having trouble connecting to the API", level='warn')
    """
    import os
    from ..utils import get_timestamp

    # Log normally first
    log(message, level=level)

    # Import here to avoid circular imports
    from ..message_queue import send_message

    # Get agent/worker info from environment
    factory_name = os.getenv('FACTORY_NAME', 'unknown')
    agent_name = os.getenv('AGENT_NAME', os.getenv('WORKER_NAME', 'unknown'))
    container_id = os.getenv('HOSTNAME', '')[:12] if os.getenv('HOSTNAME') else ''

    # Build persona log payload
    payload = {
        'factory_name': factory_name,
        'action_name': agent_name,
        'container_id': container_id,
        'message': message,
        'level': level,
        'timestamp': get_timestamp(),
    }

    if metadata:
        payload['metadata'] = metadata

    # Publish to persona_log topic
    try:
        send_message('persona_log', payload)
    except Exception as e:
        # Don't fail if message queue is unavailable
        logger.debug(f"Failed to publish persona log: {e}")
