#!/usr/bin/env python
"""
TeenyFactories - Multi-provider LLM and PostgreSQL Message Queue

A Python package for building distributed agent systems with:
- Multi-provider LLM integration (OpenAI, Anthropic, Google, Ollama, Azure Bedrock)
- PostgreSQL-based message queue (LISTEN/NOTIFY) and job queue
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
from .logging import log, log_debug, log_info, log_warn, log_error, log_persona

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

# Timer
from .timer import Timer, create_timer, run_timer_agent

# Configuration
from .config import (
    PROJECT_NAME,
    FACTORY_PREFIX,
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
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
    'log_persona',

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

    # Timer
    'Timer',
    'create_timer',
    'run_timer_agent',

    # Configuration
    'PROJECT_NAME',
    'FACTORY_PREFIX',
    'POSTGRES_HOST',
    'POSTGRES_PORT',
    'POSTGRES_DB',
    'POSTGRES_USER',
    'POSTGRES_PASSWORD',
]
