"""
Unit tests for the on_state dispatch strike state machine
(teenyfactories.message_queue.base._dispatch).

Pure-function: claims + stepped-debug are monkeypatched out and log_warn /
log_error are captured, so no DB is needed. Covers the strike key being keyed on
state_changed_at and the proactive "handler returned without advancing the row"
warning (fires once, on the first re-sighting, only for a clean no-op where the
handler actually ran).
"""

import importlib

import teenyfactories.message_queue.base as base
import teenyfactories.claims as claims
# `teenyfactories.breakpoint` the ATTRIBUTE is the re-exported public function,
# which shadows the submodule; import the submodule explicitly to patch it.
bp = importlib.import_module('teenyfactories.breakpoint')


def _reset():
    base._strikes.clear()
    base._ran_keys.clear()
    base._park_reason.clear()


def _patch(monkeypatch, claim_ok=True):
    monkeypatch.setattr(claims, 'try_claim', lambda *a, **k: claim_ok)
    monkeypatch.setattr(claims, 'release_claim', lambda *a, **k: None)
    monkeypatch.setattr(bp, '_auto_halt', lambda *a, **k: None)
    warns, errors = [], []
    monkeypatch.setattr(base, 'log_warn', lambda m: warns.append(m))
    monkeypatch.setattr(base, 'log_error', lambda m: errors.append(m))
    return warns, errors


def _item(key='k1', state='pending', sca='2026-01-01T00:00:00', coll='c'):
    # updated_at deliberately differs from state_changed_at to prove the strike
    # key follows state_changed_at, not updated_at.
    return {'collection': coll, 'state': state, 'key': key,
            'state_changed_at': sca, 'updated_at': '2026-06-06T06:06:06'}


def test_clean_noop_warns_once_on_second_sighting(monkeypatch):
    _reset()
    warns, _ = _patch(monkeypatch)
    entries = [{'handler': lambda it: None}]
    it = _item()
    base._dispatch(entries, it)              # 1st sighting — no warning yet
    assert warns == []
    base._dispatch(entries, it)              # 1st re-sighting — proactive warn
    assert len(warns) == 1
    assert 'without advancing' in warns[0]
    assert 'k1' in warns[0]
    base._dispatch(entries, it)              # further re-sightings — no repeat
    assert len(warns) == 1


def test_same_state_but_bumped_state_changed_at_is_not_flagged(monkeypatch):
    _reset()
    warns, _ = _patch(monkeypatch)
    entries = [{'handler': lambda it: None}]
    base._dispatch(entries, _item(sca='2026-01-01T00:00:00'))
    # Re-queue bounce: same state, NEW state_changed_at → new strike key → progress.
    base._dispatch(entries, _item(sca='2026-01-01T00:05:00'))
    assert warns == []


def test_updated_at_change_alone_does_not_reset_the_strike(monkeypatch):
    # A pure no-op whose updated_at happens to differ between sightings must
    # STILL warn — the key follows state_changed_at, which is unchanged.
    _reset()
    warns, _ = _patch(monkeypatch)
    entries = [{'handler': lambda it: None}]
    a = _item(sca='2026-01-01T00:00:00')
    a['updated_at'] = '2026-01-01T00:00:01'
    b = _item(sca='2026-01-01T00:00:00')
    b['updated_at'] = '2026-01-01T00:00:02'
    base._dispatch(entries, a)
    base._dispatch(entries, b)
    assert len(warns) == 1


def test_claim_skip_does_not_warn(monkeypatch):
    _reset()
    warns, _ = _patch(monkeypatch, claim_ok=False)

    def _must_not_run(it):
        raise AssertionError('handler ran despite a lost claim')

    entries = [{'handler': _must_not_run}]
    it = _item()
    base._dispatch(entries, it)
    base._dispatch(entries, it)
    assert warns == []                       # handler never ran → no no-op warn


def test_exception_path_logs_error_not_noop_warn(monkeypatch):
    _reset()
    warns, errors = _patch(monkeypatch)

    def _boom(it):
        raise ValueError('kaboom')

    entries = [{'handler': _boom}]
    it = _item()
    base._dispatch(entries, it)              # raises → _park_reason set + log_error
    base._dispatch(entries, it)              # re-sighting → exception, not clean no-op
    assert warns == []
    assert any('failed' in e for e in errors)


def test_parks_after_max_attempts(monkeypatch):
    _reset()
    warns, errors = _patch(monkeypatch)
    entries = [{'handler': lambda it: None}]
    it = _item()
    for _ in range(6):                       # 5 dispatches + the parking sighting
        base._dispatch(entries, it)
    assert any('parked' in e for e in errors)
    assert base._strikes[('k1', 'pending', '2026-01-01T00:00:00')] == base._PARKED
