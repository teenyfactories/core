"""
TeenyFactories — Python framework for distributed agent systems backed by Postgres.

All factory data lives in the `factory_data` table; every row carries a
`state` column. A subscribed `(collection, state)` IS a FIFO queue: agents
react to rows in a state via `tf.on_state`, and the handler consumes a row
by transitioning its state or deleting it. There is no message bus —
chain stages by writing the next state.

Usage:
    import teenyfactories as tf

    @tf.on_state('documents', 'loaded').do
    def handle(item):
        # item = {factory_name, collection, key, user_id, data, state,
        #         created_at, updated_at}
        # ... do the work, then move the row out of 'loaded':
        tf.collection('documents').set(item['key'], state='processed',
                                       data={**item['data'], 'analysed': True})

    @tf.on_state('documents', 'processed').do
    def downstream(item):
        ...

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
from .message_queue import on_state, run_pending

# MCP Tools
from .mcp import add_mcp_server, add_mcp_tool

# Data Collections
from .collection import collection

# Stepped debugging (no-op when factory debug mode off)
from .breakpoint import breakpoint

# Embedding
from .embedding import embed

# Configuration (factory-visible values only — connection env vars are
# internal and accessed directly via os.getenv inside the core.)
from .config import FACTORY_NAME, AGENT_NAME, AGENT_SLUG, AGENT_ID

# Scheduling — delegates to the `schedule` library.
on_schedule = _schedule.default_scheduler

# Stepped-debug hook for scheduled jobs. `tf.on_state` dispatch already
# routes through message_queue._dispatch → breakpoint._auto_halt. Scheduled
# jobs run via schedule.Job.run() which never touches _dispatch, so without
# this wrapping they bypass the auto-halt entirely (user-reported 2026-05-26).
# Monkey-patching Job.do() at import time wraps the user's callback so that
# scope='all' auto-halts fire before each invocation. scope='explicit' /
# disabled: no-op (scope check lives inside _auto_halt itself).
_orig_schedule_do = _schedule.Job.do


def _patched_schedule_do(self, job_func, *args, **kwargs):
    """Wraps `Job.do(func, *args, **kwargs)` so scope='all' halts fire before
    the scheduled callback runs. Identity preserved (`__name__`, `__wrapped__`)
    so logs + introspection still report the original function."""

    def _wrapped(*a, **kw):
        # Lazy import — breakpoint module isn't ready at __init__.py import
        # time (circular: collection → config → __init__).
        from .breakpoint import _auto_halt as _bp_auto_halt
        # Synthetic dispatch coords for the halt log row. coll='_schedule'
        # mirrors the reserved-collection convention; state=<func name> so
        # operators can see WHICH scheduled job halted.
        _bp_auto_halt('_schedule', job_func.__name__, {'key': job_func.__name__})
        return job_func(*a, **kw)

    _wrapped.__name__ = getattr(job_func, '__name__', '_wrapped')
    _wrapped.__wrapped__ = job_func
    return _orig_schedule_do(self, _wrapped, *args, **kwargs)


_schedule.Job.do = _patched_schedule_do

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
    'on_state', 'run_pending',

    # MCP Tools
    'add_mcp_server', 'add_mcp_tool',

    # Data Collections
    'collection',

    # Stepped debugging
    'breakpoint',

    # Embedding
    'embed',

    # Scheduling
    'on_schedule',

    # Sleep
    'sleep',

    # Configuration
    'FACTORY_NAME', 'AGENT_NAME', 'AGENT_SLUG', 'AGENT_ID',
]
