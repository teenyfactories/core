"""Timer module for scheduled state emissions in TeenyFactories

Timers are special nodes that emit state events on a schedule,
triggering downstream agents and workers.
"""

import os
import threading
from typing import Optional, Callable

from .logging import log_info, log_debug, log_error, log_persona
from .message_queue import send_message, schedule_task
from .utils import get_timestamp


class Timer:
    """A timer that emits state events on a configurable schedule."""

    def __init__(
        self,
        name: str,
        interval_seconds: int,
        state_name: str,
        factory_name: Optional[str] = None
    ):
        """
        Initialize a timer.

        Args:
            name: Timer identifier
            interval_seconds: Interval between emissions in seconds
            state_name: Name of state to emit (topic name)
            factory_name: Factory this timer belongs to (defaults to FACTORY_NAME env)
        """
        self.name = name
        self.interval_seconds = interval_seconds
        self.state_name = state_name
        self.factory_name = factory_name or os.getenv('FACTORY_NAME', 'demo')
        self.tick_count = 0
        self._running = False
        self._on_tick_callback: Optional[Callable] = None

    def _emit_tick(self):
        """Emit a timer tick event."""
        if not self._running:
            return

        self.tick_count += 1

        payload = {
            'timer_name': self.name,
            'factory_name': self.factory_name,
            'timestamp': get_timestamp(),
            'tick_count': self.tick_count,
            'interval_seconds': self.interval_seconds
        }

        log_info(f"Timer '{self.name}' tick #{self.tick_count}")
        log_persona(f"Tick #{self.tick_count} - emitting {self.state_name}")

        # Send the state message
        send_message(self.state_name, payload)

        # Call custom callback if set
        if self._on_tick_callback:
            try:
                self._on_tick_callback(payload)
            except Exception as e:
                log_error(f"Error in timer callback: {e}")

    def on_tick(self, callback: Callable):
        """
        Set a callback to be called on each tick.

        Args:
            callback: Function to call with tick payload
        """
        self._on_tick_callback = callback
        return self

    def start(self):
        """Start the timer."""
        if self._running:
            log_debug(f"Timer '{self.name}' already running")
            return self

        self._running = True
        log_info(f"Starting timer '{self.name}' with {self.interval_seconds}s interval")
        log_persona(f"I'm starting up and will tick every {self._format_interval()}")

        # Schedule the recurring task
        schedule_task(self._emit_tick, self.interval_seconds)

        return self

    def stop(self):
        """Stop the timer."""
        self._running = False
        log_info(f"Timer '{self.name}' stopped")
        log_persona(f"I've stopped ticking")
        return self

    def _format_interval(self) -> str:
        """Format interval for human display."""
        if self.interval_seconds >= 3600:
            hours = self.interval_seconds // 3600
            return f"{hours}h"
        elif self.interval_seconds >= 60:
            mins = self.interval_seconds // 60
            return f"{mins}m"
        else:
            return f"{self.interval_seconds}s"

    @property
    def is_running(self) -> bool:
        """Check if timer is currently running."""
        return self._running


def create_timer(
    name: str,
    interval_seconds: int,
    state_name: str,
    factory_name: Optional[str] = None,
    auto_start: bool = True
) -> Timer:
    """
    Create and optionally start a timer.

    Args:
        name: Timer identifier
        interval_seconds: Interval between emissions in seconds
        state_name: Name of state to emit
        factory_name: Factory name (defaults to FACTORY_NAME env)
        auto_start: Whether to start the timer immediately

    Returns:
        Timer instance

    Example:
        >>> timer = create_timer('heartbeat', 300, 'timer_tick')  # 5 minutes
        >>> # Timer is now running and will emit 'timer_tick' every 5 minutes
    """
    timer = Timer(name, interval_seconds, state_name, factory_name)
    if auto_start:
        timer.start()
    return timer


def run_timer_agent(
    timer_name: str,
    interval_seconds: int,
    state_name: str
):
    """
    Run a standalone timer agent.

    This is the main entry point for timer containers.
    It creates a timer and runs the main event loop.

    Args:
        timer_name: Timer identifier
        interval_seconds: Interval between emissions
        state_name: State to emit

    Example:
        >>> # In timer container script:
        >>> run_timer_agent('5min_trigger', 300, 'timer_tick')
    """
    from .message_queue import wait_for_next_message_or_scheduled_task

    log_info(f"Starting timer agent: {timer_name}")
    log_persona(f"Hello! I'm the {timer_name} timer and I'll tick every {interval_seconds}s")

    # Create and start timer
    timer = create_timer(timer_name, interval_seconds, state_name)

    # Run the main event loop
    wait_for_next_message_or_scheduled_task()
