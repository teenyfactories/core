"""
TeenyFactories - Multi-provider LLM and PostgreSQL Message Queue

Usage:
    import teenyfactories as tf

    def handle(message):
        data = message.get('data', {})
        tf.send_message('output').with_data({'result': 'done'})

    tf.on_message('input_topic').do(handle)
    tf.on_schedule.every(10).minutes.do(some_job)

    while True:
        tf.run_pending()
        tf.sleep(1)
"""

import time as _time
import schedule as _schedule

from .__version__ import __version__

# Logging
from .logging import log, log_debug, log_info, log_warn, log_error, log_persona

# Utilities
from .utils import get_aest_now, get_timestamp, generate_unique_id, AEST_TIMEZONE

# LLM
from .llm import get_llm_client, call_llm, clean_json_response

# Message Queue
from .message_queue import send_message, on_message, run_pending

# Chat Tools
from .chat_tools import chat_tool, start_chat_tools

# Data Store
from .store import store

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

# Scheduling — delegates to the schedule library
on_schedule = _schedule.default_scheduler

# Sleep — so scripts only need `import teenyfactories as tf`
sleep = _time.sleep

__all__ = [
    '__version__',

    # Logging
    'log', 'log_debug', 'log_info', 'log_warn', 'log_error', 'log_persona',

    # Utilities
    'get_aest_now', 'get_timestamp', 'generate_unique_id', 'AEST_TIMEZONE',

    # LLM
    'get_llm_client', 'call_llm', 'clean_json_response',

    # Message Queue
    'send_message', 'on_message', 'run_pending',

    # Chat Tools
    'chat_tool', 'start_chat_tools',

    # Data Store
    'store',

    # Scheduling
    'on_schedule',

    # Sleep
    'sleep',

    # Configuration
    'PROJECT_NAME', 'FACTORY_PREFIX',
    'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
]
