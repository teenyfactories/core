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
