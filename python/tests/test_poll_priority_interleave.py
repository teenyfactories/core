"""
Unit tests for on_state `.priority()` ordering and the scheduled-job interleave
in teenyfactories.message_queue.base._poll_pass.

Pure-function: a fake provider supplies rows, claims + stepped-debug are
monkeypatched out, and `_run_scheduled_jobs` is replaced with a counter, so no
DB is needed. Covers:
  - lower priority number runs sooner (nice semantics), default 0, negatives
    ahead / positives behind;
  - equal priority keeps registration (FIFO) order;
  - the scheduler is re-run once per state row (the yield that stops a long
    state drain from starving a due on_schedule job — the simply incident).
"""

import importlib

import teenyfactories.message_queue.base as base
import teenyfactories.claims as claims

bp = importlib.import_module("teenyfactories.breakpoint")


class _FakeProvider:
    def __init__(self, rows):
        self._rows = rows  # {(coll, state): [items]}

    def fetch_rows(self, coll, state):
        return list(self._rows.get((coll, state), []))

    def fetch_due_rows(self, coll, state, d):
        return []


def _reset_all():
    base._handlers.clear()
    base._strikes.clear()
    base._ran_keys.clear()
    base._park_reason.clear()


def _patch_common(monkeypatch):
    monkeypatch.setattr(claims, "try_claim", lambda *a, **k: True)
    monkeypatch.setattr(claims, "release_claim", lambda *a, **k: None)
    monkeypatch.setattr(bp, "_auto_halt", lambda *a, **k: None)
    monkeypatch.setattr(base, "log_warn", lambda m: None)
    monkeypatch.setattr(base, "log_error", lambda m: None)


def _register(coll, state, handler, priority=0, delay=0.0):
    base._handlers.setdefault((coll, state), []).append(
        {"handler": handler, "delay_seconds": delay, "claim_duration_seconds": 3600.0, "priority": priority}
    )


def _item(coll, state, key):
    return {
        "collection": coll,
        "state": state,
        "key": key,
        "state_changed_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


def test_lower_priority_runs_sooner(monkeypatch):
    _reset_all()
    _patch_common(monkeypatch)
    order = []
    # Registered default → boosted → background; served -5, 0, 3.
    _register("c", "normal", lambda it: order.append("normal"), priority=0)
    _register("c", "boosted", lambda it: order.append("boosted"), priority=-5)
    _register("c", "background", lambda it: order.append("background"), priority=3)
    rows = {
        ("c", "normal"): [_item("c", "normal", "a")],
        ("c", "boosted"): [_item("c", "boosted", "b")],
        ("c", "background"): [_item("c", "background", "d")],
    }
    monkeypatch.setattr(base, "_get_provider", lambda: _FakeProvider(rows))
    monkeypatch.setattr(base, "_run_scheduled_jobs", lambda: None)
    base._poll_pass()
    assert order == ["boosted", "normal", "background"]


def test_equal_priority_keeps_registration_order(monkeypatch):
    _reset_all()
    _patch_common(monkeypatch)
    order = []
    _register("c", "first", lambda it: order.append("first"), priority=0)
    _register("c", "second", lambda it: order.append("second"), priority=0)
    rows = {
        ("c", "first"): [_item("c", "first", "a")],
        ("c", "second"): [_item("c", "second", "b")],
    }
    monkeypatch.setattr(base, "_get_provider", lambda: _FakeProvider(rows))
    monkeypatch.setattr(base, "_run_scheduled_jobs", lambda: None)
    base._poll_pass()
    assert order == ["first", "second"]


def test_scheduler_reruns_once_per_row(monkeypatch):
    """The starvation fix: during a multi-row state drain, the scheduler is
    re-run between every row so a due on_schedule job fires mid-drain, not after."""
    _reset_all()
    _patch_common(monkeypatch)
    sched = {"n": 0}
    dispatched = {"n": 0}
    _register("c", "drain", lambda it: dispatched.__setitem__("n", dispatched["n"] + 1))
    rows = {("c", "drain"): [_item("c", "drain", f"k{i}") for i in range(5)]}
    monkeypatch.setattr(base, "_get_provider", lambda: _FakeProvider(rows))
    monkeypatch.setattr(base, "_run_scheduled_jobs", lambda: sched.__setitem__("n", sched["n"] + 1))
    base._poll_pass()
    assert dispatched["n"] == 5
    assert sched["n"] == 5  # re-run once per row — the yield


def test_builder_priority_flows_to_registration(monkeypatch):
    monkeypatch.setattr(base, "log_warn", lambda m: None)
    base._pending_registrations.clear()
    base.on_state("c", "s").priority(-2).do(lambda it: None)
    assert base._pending_registrations[-1]["priority"] == -2
    base.on_state("c", "s2").do(lambda it: None)  # default
    assert base._pending_registrations[-1]["priority"] == 0
    base._pending_registrations.clear()
