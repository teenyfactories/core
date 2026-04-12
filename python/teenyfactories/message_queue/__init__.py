"""Message queue abstraction for teenyfactories"""

from .base import (
    send_message,
    on_message,
    run_pending,
)

__all__ = [
    'send_message',
    'on_message',
    'run_pending',
]
