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
from .utils import get_aest_now, get_timestamp, generate_unique_id

# LLM
from .llm import call_llm

# Message Queue
from .message_queue import send_message, on_message, on_state, run_pending

# MCP Tools
from .mcp import add_mcp_server, add_mcp_tool

# Data Store
from .store import store

# Embedding
from .embedding import embed

# Configuration (factory-visible values only — connection env vars are
# internal and accessed directly via os.getenv inside the core.)
from .config import PROJECT_NAME, FACTORY_PREFIX

# Scheduling — delegates to the schedule library
on_schedule = _schedule.default_scheduler

# Sleep — so scripts only need `import teenyfactories as tf`
sleep = _time.sleep

__all__ = [
    '__version__',

    # Logging
    'log', 'log_debug', 'log_info', 'log_warn', 'log_error', 'log_persona',

    # Utilities
    'get_aest_now', 'get_timestamp', 'generate_unique_id',

    # LLM
    'call_llm',

    # Message Queue
    'send_message', 'on_message', 'on_state', 'run_pending',

    # MCP Tools
    'add_mcp_server', 'add_mcp_tool',

    # Data Store
    'store',

    # Embedding
    'embed',

    # Scheduling
    'on_schedule',

    # Sleep
    'sleep',

    # Configuration
    'PROJECT_NAME', 'FACTORY_PREFIX',
]
