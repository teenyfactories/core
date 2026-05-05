"""
TeenyFactories — Python framework for distributed agent systems backed by Postgres.

All factory data lives in the `factory_data` table; every row carries a
`state` column that drives pub/sub via Postgres LISTEN/NOTIFY. Agents react
to state transitions on real lifecycle rows (`tf.on_state`) or to
fire-and-forget signals on the `_messages` collection (`tf.on_message`).

Usage:
    import teenyfactories as tf

    @tf.on_state('documents', 'loaded').do
    def handle(item):
        # item = {factory_name, collection, key, user_id, data, state,
        #         created_at, updated_at}
        tf.collection('documents').set(item['key'], state='processed')
        tf.send_message('analysis_done').with_data({'key': item['key']})

    tf.on_schedule.every(10).minutes.do(periodic_job)

    while True:
        tf.run_pending()
        tf.sleep(1)
"""

import time as _time
import schedule as _schedule

from .__version__ import __version__

# Logging
from .logging import log_debug, log_info, log_warn, log_error, log_persona

# Utilities
from .utils import get_timestamp, get_timestamp_utc, generate_unique_id

# LLM
from .llm import call_llm

# Secrets — pull from orchestrator's in-built secrets store with env-var fallback.
from .secrets import secrets

# Message Queue
from .message_queue import send_message, on_message, on_state, run_pending

# MCP Tools
from .mcp import add_mcp_server, add_mcp_tool

# Data Collections
from .collection import collection

# Embedding
from .embedding import embed

# Configuration (factory-visible values only — connection env vars are
# internal and accessed directly via os.getenv inside the core.)
from .config import FACTORY_NAME, AGENT_NAME, AGENT_ID

# Scheduling — delegates to the schedule library
on_schedule = _schedule.default_scheduler

# Sleep — so scripts only need `import teenyfactories as tf`
sleep = _time.sleep

__all__ = [
    '__version__',

    # Logging
    'log_debug', 'log_info', 'log_warn', 'log_error', 'log_persona',

    # Utilities
    'get_timestamp', 'get_timestamp_utc', 'generate_unique_id',

    # LLM
    'call_llm',

    # Secrets
    'secrets',

    # Message Queue
    'send_message', 'on_message', 'on_state', 'run_pending',

    # MCP Tools
    'add_mcp_server', 'add_mcp_tool',

    # Data Collections
    'collection',

    # Embedding
    'embed',

    # Scheduling
    'on_schedule',

    # Sleep
    'sleep',

    # Configuration
    'FACTORY_NAME', 'AGENT_NAME', 'AGENT_ID',
]
