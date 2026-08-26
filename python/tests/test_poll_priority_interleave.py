"""
Unit tests for the tiered, preemptive dispatch pass in
teenyfactories.message_queue.base._poll_pass and its helpers.

Pure-function: a fake provider models `peek_next` (a transitioned row simply
disappears), claims + stepped-debug are monkeypatched out, and scheduled jobs
are supplied via a stub, so no DB is needed. Covers:
  - scheduled ⨝ state competition by `.priority()` via the scheduled-priority
    BOUND: a state row runs before a scheduled job only when it beats it;
  - the per-pass `seen` guard stops a no-op row (handler didn't transition it)
    from being re-served forever inside one pass;
  - `_subscription_rows` tiering (MCP `_mcp_*` = tier 0) + min priority/delay;
  - `_get_next_scheduled_job` picks lowest priority then soonest;
  - `_due_entries` honours each entry's own delay;
  - `.priority()` flows to registration for BOTH on_state and on_schedule.

The MCP-first tier and the SQL FIFO/priority ORDER BY live inside `peek_next`
(postgres.py) and need a PG integration test, not this pure-Python suite.
"""

import importlib
from datetime import datetime, timezone

import teenyfactories.message_queue.base as base
import teenyfactories.claims as claims

bp = importlib.import_module("teenyfactories.breakpoint")


class _FakeProvider:
    """Models peek_next over a set of ready rows. Each row carries its own
    (_tier, _priority) so the fake reproduces the SQL ordering; a handler
    'transitions' a row by calling .remove (real rows leave the state, so the
    next peek no longer returns them)."""

    def __init__(self, rows):
        self.rows = list(rows)

    def peek_next(self, subs, bound):
        elig = [r for r in self.rows if r["_tier"] == 0 or r["_priority"] < bound]
        if not elig:
            return None
        elig.sort(key=lambda r: (r["_tier"], r["_priority"], r["state_changed_at"], r["key"]))
        return elig[0]

    def remove(self, r):
        self.rows = [
            x
            for x in self.rows
            if not (x["collection"] == r["collection"] and x["key"] == r["key"] and x["state"] == r["state"])
        ]


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


def _item(coll, state, key, tier=1, priority=0, sca="2020-01-01T00:00:00"):
    return {
        "collection": coll,
        "state": state,
        "key": key,
        "state_changed_at": sca,
        "updated_at": sca,
        "_tier": tier,
        "_priority": priority,
    }


def _one_due_scheduler(state, priority=0):
    """Stub for base._get_next_scheduled_job: one due job at `priority` that
    records its run order and becomes not-due after running once."""

    def fake():
        if not state["due"]:
            return None

        def run():
            state["order"].append("sched")
            state["due"] = False

        return (priority, run)

    return fake


# ---------------------------------------------------------------------------
# Scheduled ⨝ state competition (the bound)
# ---------------------------------------------------------------------------


def test_state_row_loses_to_higher_priority_scheduled(monkeypatch):
    """State handler priority 5, scheduled job priority 0 → scheduled runs first
    (state row 5 does NOT beat the bound 0), then the state row."""
    _reset_all()
    _patch_common(monkeypatch)
    sstate = {"due": True, "order": []}
    _register("c", "s", lambda it: (sstate["order"].append("state"), prov.remove(it)), priority=5)
    prov = _FakeProvider([_item("c", "s", "a", tier=1, priority=5)])
    monkeypatch.setattr(base, "_get_provider", lambda: prov)
    monkeypatch.setattr(base, "_get_next_scheduled_job", _one_due_scheduler(sstate, priority=0))
    base._poll_pass(do_db_poll=True)
    assert sstate["order"] == ["sched", "state"]


def test_state_row_beats_lower_priority_scheduled(monkeypatch):
    """State handler priority -5, scheduled job priority 0 → state row runs first
    (it beats the bound 0), then the scheduled job."""
    _reset_all()
    _patch_common(monkeypatch)
    sstate = {"due": True, "order": []}
    _register("c", "s", lambda it: (sstate["order"].append("state"), prov.remove(it)), priority=-5)
    prov = _FakeProvider([_item("c", "s", "a", tier=1, priority=-5)])
    monkeypatch.setattr(base, "_get_provider", lambda: prov)
    monkeypatch.setattr(base, "_get_next_scheduled_job", _one_due_scheduler(sstate, priority=0))
    base._poll_pass(do_db_poll=True)
    assert sstate["order"] == ["state", "sched"]


def test_no_db_poll_runs_only_scheduled(monkeypatch):
    """do_db_poll=False (between polls) never touches the provider — only due
    scheduled jobs run (the in-memory heartbeat)."""
    _reset_all()
    _patch_common(monkeypatch)
    sstate = {"due": True, "order": []}
    hit = {"peeked": False}

    class _Boom:
        def peek_next(self, *a, **k):
            hit["peeked"] = True
            raise AssertionError("provider must not be touched when do_db_poll=False")

    _register("c", "s", lambda it: sstate["order"].append("state"), priority=0)
    monkeypatch.setattr(base, "_get_provider", lambda: _Boom())
    monkeypatch.setattr(base, "_get_next_scheduled_job", _one_due_scheduler(sstate, priority=0))
    base._poll_pass(do_db_poll=False)
    assert sstate["order"] == ["sched"]
    assert hit["peeked"] is False


# ---------------------------------------------------------------------------
# Per-pass seen guard
# ---------------------------------------------------------------------------


def test_noop_row_served_once_per_pass(monkeypatch):
    """A handler that does NOT transition its row leaves peek_next returning the
    SAME row forever; the per-pass `seen` set must serve it exactly once and end
    the pass (not spin to the unit cap)."""
    _reset_all()
    _patch_common(monkeypatch)
    calls = {"n": 0}
    _register("c", "s", lambda it: calls.__setitem__("n", calls["n"] + 1), priority=0)
    prov = _FakeProvider([_item("c", "s", "a")])  # handler never removes it
    monkeypatch.setattr(base, "_get_provider", lambda: prov)
    monkeypatch.setattr(base, "_get_next_scheduled_job", lambda: None)
    base._poll_pass(do_db_poll=True)
    assert calls["n"] == 1


def test_two_distinct_rows_both_served(monkeypatch):
    """Two ready rows that transition on handling are each served once."""
    _reset_all()
    _patch_common(monkeypatch)
    seen_keys = []
    _register("c", "s", lambda it: (seen_keys.append(it["key"]), prov.remove(it)), priority=0)
    prov = _FakeProvider([_item("c", "s", "a"), _item("c", "s", "b")])
    monkeypatch.setattr(base, "_get_provider", lambda: prov)
    monkeypatch.setattr(base, "_get_next_scheduled_job", lambda: None)
    base._poll_pass(do_db_poll=True)
    assert sorted(seen_keys) == ["a", "b"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_subscription_rows_tiers_and_mins():
    _reset_all()
    _register("_mcp_query", "request", lambda it: None, priority=0, delay=0.0)
    _register("orders", "new", lambda it: None, priority=3, delay=5.0)
    rows = dict(((c, s), (t, p, d)) for (c, s, t, p, d) in base._subscription_rows())
    assert rows[("_mcp_query", "request")] == (0, 0, 0.0)  # MCP → tier 0
    assert rows[("orders", "new")] == (1, 3, 5.0)  # ordinary → tier 1


def test_get_next_scheduled_job_picks_lowest_then_soonest(monkeypatch):
    class _FakeJob:
        def __init__(self, should_run, priority, next_run):
            self.should_run = should_run
            self._tf_priority = priority
            self.next_run = next_run
            self.ran = 0

        def run(self):
            self.ran += 1

    not_due = _FakeJob(False, -9, 1)  # ignored — not due
    low = _FakeJob(True, -2, 5)  # winner (lowest priority)
    tie_early = _FakeJob(True, 0, 2)
    tie_late = _FakeJob(True, 0, 9)
    monkeypatch.setattr(base._schedule, "jobs", [not_due, tie_late, low, tie_early])
    picked = base._get_next_scheduled_job()
    assert picked is not None
    assert picked[0] == -2
    picked[1]()
    assert low.ran == 1 and not_due.ran == 0

    # Among equal priority, soonest next_run wins.
    monkeypatch.setattr(base._schedule, "jobs", [tie_late, tie_early])
    picked = base._get_next_scheduled_job()
    picked[1]()
    assert tie_early.ran == 1 and tie_late.ran == 0


def test_get_next_scheduled_job_none_when_nothing_due(monkeypatch):
    monkeypatch.setattr(base._schedule, "jobs", [])
    assert base._get_next_scheduled_job() is None


def test_due_entries_honours_delay():
    old = {"state_changed_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}
    now = {"state_changed_at": datetime.now(timezone.utc)}
    entries = [{"handler": lambda i: None, "delay_seconds": 0.0}, {"handler": lambda i: None, "delay_seconds": 100.0}]
    assert len(base._due_entries(entries, old)) == 2  # both elapsed
    due_now = base._due_entries(entries, now)
    assert len(due_now) == 1 and due_now[0]["delay_seconds"] == 0.0


# ---------------------------------------------------------------------------
# Builder .priority() → registration, for BOTH surfaces
# ---------------------------------------------------------------------------


def test_on_state_priority_flows_to_registration(monkeypatch):
    monkeypatch.setattr(base, "log_warn", lambda m: None)
    base._pending_registrations.clear()
    base.on_state("c", "s").priority(-2).do(lambda it: None)
    assert base._pending_registrations[-1]["priority"] == -2
    base.on_state("c", "s2").do(lambda it: None)  # default
    assert base._pending_registrations[-1]["priority"] == 0
    base._pending_registrations.clear()


def test_on_schedule_priority_attaches_to_job():
    job = base._schedule.every(10).seconds
    try:
        assert job.priority(-3) is job  # fluent — returns the Job
        assert job._tf_priority == -3
        # A job that never had .priority() called reads as 0 via getattr default.
        assert getattr(base._schedule.every(5).seconds, "_tf_priority", 0) == 0
    finally:
        base._schedule.clear()
