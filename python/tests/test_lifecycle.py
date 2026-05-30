"""Unit tests for teenyfactories.lifecycle — graceful SIGTERM/SIGINT shutdown.

These tests deliver real signals to the current process via
``os.kill(os.getpid(), SIG…)``. They depend ONLY on the lifecycle module
(no DB, no provider), so they run anywhere Python runs.
"""

import logging
import os
import signal
import time

import pytest

from teenyfactories import lifecycle


@pytest.fixture(autouse=True)
def _reset_lifecycle():
    """Clear lifecycle module state and previous SIGTERM/SIGINT handlers
    so each test starts from a clean slate."""
    # Save the OS-level handlers so we can restore them after the test.
    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    lifecycle._reset_for_tests()
    yield
    lifecycle._reset_for_tests()
    try:
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGINT, prev_int)
    except (ValueError, OSError, TypeError):
        pass


def test_shutting_down_starts_false():
    assert lifecycle.shutting_down() is False


def test_install_signal_handlers_is_idempotent():
    lifecycle.install_signal_handlers()
    assert lifecycle._handlers_installed is True
    # Capture the handler reference and verify a second call doesn't swap it.
    handler_after_first = signal.getsignal(signal.SIGTERM)
    lifecycle.install_signal_handlers()
    assert signal.getsignal(signal.SIGTERM) is handler_after_first


def test_sigterm_sets_flag_and_exits_run_loop():
    """Simulates the canonical agent loop. SIGTERM delivered mid-loop
    should set the flag, then cause exit_if_shutting_down to raise
    SystemExit(0) on the next checkpoint."""
    lifecycle.install_signal_handlers()
    iterations = 0
    with pytest.raises(SystemExit) as exc_info:
        while True:
            iterations += 1
            if iterations == 1:
                os.kill(os.getpid(), signal.SIGTERM)
            # Tiny sleep + check, mimicking what run_pending() does at
            # the end of every tick.
            time.sleep(0.01)
            lifecycle.exit_if_shutting_down()
            # Hard cap so a regression can't hang CI.
            assert iterations < 100, "SIGTERM was not observed"
    assert exc_info.value.code == 0
    assert lifecycle.shutting_down() is True


def test_sigint_sets_flag_and_exits():
    """SIGINT (Ctrl-C) must behave the same as SIGTERM."""
    lifecycle.install_signal_handlers()
    os.kill(os.getpid(), signal.SIGINT)
    # Give the signal a moment to be delivered to the Python interpreter.
    time.sleep(0.05)
    with pytest.raises(SystemExit) as exc_info:
        lifecycle.exit_if_shutting_down()
    assert exc_info.value.code == 0


def test_sleep_wakes_early_on_sigterm():
    """tf.sleep(N) must return early (via SystemExit) when SIGTERM
    arrives mid-sleep, instead of blocking for the full duration."""
    lifecycle.install_signal_handlers()

    # Schedule a SIGTERM ~50 ms from now using a background thread.
    import threading

    def _deliver():
        time.sleep(0.05)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_deliver, daemon=True).start()
    start = time.monotonic()
    with pytest.raises(SystemExit) as exc_info:
        lifecycle.sleep(5.0)  # would block 5s without the early exit
    elapsed = time.monotonic() - start
    assert exc_info.value.code == 0
    assert elapsed < 2.0, f"sleep didn't wake early: took {elapsed:.2f}s"


def test_second_signal_does_not_relog(caplog):
    """A second SIGTERM in the same process should not produce a second
    'shutting down' log line. The first one is the only operator-relevant
    one; further signals would just spam the log."""
    lifecycle.install_signal_handlers()
    with caplog.at_level(logging.INFO):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        # Force the first signal to be processed.
        try:
            lifecycle.exit_if_shutting_down()
        except SystemExit:
            pass
        first_count = sum(
            1 for r in caplog.records if "shutting down" in r.getMessage()
        )

        # Second signal — handler runs but should not re-log.
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        second_count = sum(
            1 for r in caplog.records if "shutting down" in r.getMessage()
        )

    assert first_count == 1, f"expected exactly one shutdown log, got {first_count}"
    assert second_count == first_count, (
        f"second SIGTERM logged again (count went {first_count} → {second_count})"
    )


def test_install_skipped_off_main_thread():
    """signal.signal raises on non-main threads, so install must no-op
    silently when called from a worker thread."""
    import threading

    results = {}

    def _worker():
        # _handlers_installed should remain False after this call.
        lifecycle.install_signal_handlers()
        results['installed'] = lifecycle._handlers_installed

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert results['installed'] is False


def test_exit_if_shutting_down_is_noop_when_flag_clear():
    """The end-of-tick check must be a cheap no-op in the common case."""
    lifecycle.install_signal_handlers()
    # Must not raise.
    lifecycle.exit_if_shutting_down()
    assert lifecycle.shutting_down() is False
