#!/usr/bin/env python
"""
TeenyFactories - Multi-provider LLM and Message Queue abstraction

A Python package for building distributed agent systems with:
- Multi-provider LLM integration (OpenAI, Anthropic, Google, Ollama, Azure Bedrock)
- Pluggable message queue backends (Redis, PostgreSQL)
- Standardized logging and utilities

Usage:
    import teenyfactories as tf

    # LLM calls
    response = tf.call_llm(prompt, inputs, response_model=MyModel)

    # Message queue
    tf.send_message('my_topic', {'status': 'completed'})
    tf.subscribe_to_message(callback, topics=['events'])

    # Logging
    tf.log("Processing started", level='info')
"""

from .__version__ import __version__

# Logging
from .logging import log, log_debug, log_info, log_warn, log_error

# Utilities
from .utils import get_aest_now, get_timestamp, generate_unique_id, AEST_TIMEZONE

# LLM
from .llm import get_llm_client, call_llm, clean_json_response

# Message Queue
from .message_queue import (
    send_message,
    subscribe_to_message,
    schedule_task,
    wait_for_next_message_or_scheduled_task,
    publish_service_status,
    set_agent_ready,
    is_agent_ready,
    acquire_processing_lock,
    release_processing_lock,
)

# Configuration
from .config import (
    PROJECT_NAME,
    FACTORY_PREFIX,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_DB,
)

__all__ = [
    # Version
    '__version__',

    # Logging
    'log',
    'log_debug',
    'log_info',
    'log_warn',
    'log_error',

    # Utilities
    'get_aest_now',
    'get_timestamp',
    'generate_unique_id',
    'AEST_TIMEZONE',

    # LLM
    'get_llm_client',
    'call_llm',
    'clean_json_response',

    # Message Queue
    'send_message',
    'subscribe_to_message',
    'schedule_task',
    'wait_for_next_message_or_scheduled_task',
    'publish_service_status',
    'set_agent_ready',
    'is_agent_ready',
    'acquire_processing_lock',
    'release_processing_lock',

    # Configuration
    'PROJECT_NAME',
    'FACTORY_PREFIX',
    'REDIS_HOST',
    'REDIS_PORT',
    'REDIS_DB',
]
