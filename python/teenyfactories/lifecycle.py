"""Process lifecycle: graceful shutdown on SIGTERM / SIGINT.

The canonical agent loop is:

    while True:
        tf.run_pending()
        tf.sleep(1)

Docker (and Kubernetes) terminate containers by sending SIGTERM, waiting
the configured grace period, then SIGKILL. Without a handler, Python's
default behaviour for SIGTERM is to ignore it (the process keeps looping),
and the orchestrator always pays the full grace period before SIGKILL.

This module installs SIGTERM + SIGINT handlers on the first
``tf.run_pending()`` tick. The handler is intentionally minimal: it sets
``_shutdown_requested = True`` and logs once. The next time
``run_pending()`` finishes a tick — or the next internal sleep slice in
``tf.sleep()`` — the flag is observed and ``sys.exit(0)`` raises
``SystemExit`` out of the user's ``while True``. Python then runs normal
cleanup (atexit hooks, generator finalisers, context-manager ``__exit__``).

Trade-offs (locked by the user):
  * Long-running in-flight LLM calls are NOT interrupted; they finish in
    the current ``run_pending`` tick (or, if the agent is sleeping, exit
    early on the next sleep slice). Docker SIGKILL is the backstop.
  * No ``tf.run_forever()`` primitive — the existing loop shape is
    preserved.
  * No ``stop_grace_period`` config change.

Handler installation is idempotent and guarded by
``threading.current_thread() is threading.main_thread()`` because
``signal.signal`` only works on the main thread.
"""

import os
import signal
import sys
import threading

from teenyfactories.logging import log_info


# Module-level state. Single-process scope.
_shutdown_requested: bool = False
_handlers_installed: bool = False
_shutdown_logged: bool = False


def shutting_down() -> bool:
    """Return True once SIGTERM/SIGINT has been received.

    Optional surface for handlers that want to bail out of long-running
    work early. The main ``while True: tf.run_pending(); tf.sleep(1)``
    loop does not need to call this — ``run_pending`` and ``sleep``
    raise ``SystemExit`` on their own once the flag is set.
    """
    return _shutdown_requested


def _handle_signal(signum, _frame):
    """Signal handler: flip the flag, log once, return.

    Signal handlers must be reentrant-safe and minimal. We do NOT raise
    or exit here — the next ``run_pending`` / ``sleep`` checkpoint will
    do that on the main thread, where teardown is well-defined.
    """
    global _shutdown_requested, _shutdown_logged
    _shutdown_requested = True
    if not _shutdown_logged:
        _shutdown_logged = True
        try:
            name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            name = f"signal {signum}"
        log_info(f"{name} received, shutting down after current tick")


def install_signal_handlers() -> None:
    """Install SIGTERM + SIGINT handlers. Idempotent.

    Skipped silently when called off the main thread (``signal.signal``
    raises ``ValueError`` otherwise), or when handlers are already
    installed.
    """
    global _handlers_installed
    if _handlers_installed:
        return
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except (ValueError, OSError):
        # ValueError: not on main thread (race with the guard above).
        # OSError: signal not supported on this platform (Windows edge).
        return
    _handlers_installed = True


def exit_if_shutting_down() -> None:
    """Raise SystemExit(0) if a shutdown signal has been received.

    Called at the end of ``run_pending()`` and inside ``sleep()`` slices.
    Returns silently otherwise.
    """
    if _shutdown_requested:
        sys.exit(0)


# Slice size for the chunked sleep. SIGTERM is observed within one slice.
# 1s is fine — no use case for sub-second shutdown. Internal, not public.
_SLEEP_SLICE_SEC = 1.0


def sleep(seconds: float) -> None:
    """Drop-in replacement for ``time.sleep`` that wakes on SIGTERM/SIGINT.

    Sleeps in small slices, checking the shutdown flag between each. A
    pending shutdown raises ``SystemExit(0)`` before the full duration
    elapses. Behaviour is otherwise identical to ``time.sleep``.
    """
    # Lazy import to avoid pulling time into module load order.
    import time as _time
    if seconds <= 0:
        exit_if_shutting_down()
        return
    remaining = float(seconds)
    while remaining > 0:
        exit_if_shutting_down()
        slice_dur = _SLEEP_SLICE_SEC if remaining > _SLEEP_SLICE_SEC else remaining
        _time.sleep(slice_dur)
        remaining -= slice_dur
    exit_if_shutting_down()


def _reset_for_tests() -> None:
    """Test-only: clear module state so tests start from a clean slate.

    Does NOT uninstall already-registered signal handlers (Python signal
    state isn't cleanly reversible); the ``_handlers_installed`` flag is
    cleared so ``install_signal_handlers`` will re-arm them.
    """
    global _shutdown_requested, _handlers_installed, _shutdown_logged
    _shutdown_requested = False
    _handlers_installed = False
    _shutdown_logged = False
