"""LLM spend-limit clearance gate — INTERNAL ONLY (not exported on the tf surface).

Cost is no longer computed or limit-checked tf-side. The orchestrator owns
pricing (computed at READ from the verbatim usage `raw`) AND limit enforcement.
Before an agent issues an LLM call, tf asks the orchestrator whether the call is
cleared to proceed. A breached spend limit PAUSES the agent (same UX the old
DB-polling gate had) rather than erroring the call.

Transport mirrors ``secrets.py`` — the orchestrator's internal HTTP listener at
``http://orchestrator:8998`` (never published to host; reachable only from
inside the private agent network). Trust is anchored on private-network
membership: the orchestrator resolves the caller's factory/agent scope from the
source IP, so — UNLIKE secrets — we send NO identity headers and NO body. The
endpoint is a pure GET.

Endpoint contract (owned by @security-architect — see PINNED CONTRACT note in
the architect's report):
    GET http://orchestrator:8998/llm-clearance
        → 200 {cleared: bool, resets_at: <iso8601|null>, scope?: <str>}

    cleared    — true ⇒ proceed; false ⇒ a limit is breached, pause.
    resets_at  — ISO-8601 instant the breach clears (when cleared=false), or
                 null if unknown. Drives how long we pause before re-checking.
    scope      — optional label of the breached limit (agent / factory /
                 tenant / default), surfaced in the pause warning.

Failure-mode policy (fail-OPEN — never block real work on an orchestrator
hiccup; same posture as the old DB poll-gate):
    200 + {cleared:true}    → proceed, cache the verdict for _CACHE_TTL_SEC
    200 + {cleared:false}   → pause via tf.sleep until resets_at, re-check
    404 / 503               → feature off ⇒ proceed (latch 503 for the process)
    5xx / network / timeout → log_warn ONCE per reason, proceed (fail-open)
    malformed JSON          → proceed (fail-open)
    never raise

Cache: a ``cleared`` verdict is trusted for _CACHE_TTL_SEC so we don't round-
trip per call on the hot path. A ``not cleared`` verdict is never cached — we
loop on fresh reads until clear.
"""

import os
import time

import requests

from .logging import log_debug, log_warn

_DEFAULT_BASE_URL = "http://orchestrator:8998"
_TIMEOUT_SECONDS = 2.0

# An "all clear" verdict stays trusted this long before we re-query. Mirrors the
# old cost_limits._CACHE_TTL_SEC — keeps the round-trip off the per-call path
# while bounding how long a freshly-breached limit goes unnoticed.
_CACHE_TTL_SEC = 30.0

# Hard floor on a pause sleep so a slightly-stale / clock-skewed resets_at can't
# spin us in a tight re-query loop.
_MIN_PAUSE_SEC = 5.0
# Cap a single sleep slice so we re-evaluate periodically even for a far-off
# (e.g. monthly) reset — the limit may be edited/disabled while we wait.
_MAX_PAUSE_SLICE_SEC = 300.0

# Process-lifetime latch: a 503 means the clearance feature is off. Once seen,
# every subsequent check proceeds without round-tripping. Restart to re-probe.
_feature_disabled = False

# Cache of the last cleared verdict (monotonic timestamp).
_last_clear_ts: float = 0.0

# Dedupe transport-failure warns so a flapping orchestrator doesn't flood logs.
_warned: set = set()


def _base_url() -> str:
    # Reuses the same override knob as secrets.py — one internal base URL for
    # every agent→orchestrator :8998 call.
    return os.getenv("TF_SECRETS_URL", _DEFAULT_BASE_URL).rstrip("/")


def _warn_once(reason: str) -> None:
    if reason in _warned:
        return
    _warned.add(reason)
    log_debug(f"[cost_clearance] clearance endpoint unreachable ({reason}); proceeding (fail-open)")


def _seconds_until(resets_at_iso) -> float:
    """Seconds from now until an ISO-8601 instant. Floored at _MIN_PAUSE_SEC."""
    if not resets_at_iso:
        return _MIN_PAUSE_SEC
    try:
        from datetime import datetime, timezone

        # Tolerate a trailing 'Z'.
        s = resets_at_iso.replace("Z", "+00:00") if isinstance(resets_at_iso, str) else resets_at_iso
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
    except Exception:
        return _MIN_PAUSE_SEC
    return max(_MIN_PAUSE_SEC, delta)


def _fetch_clearance():
    """GET /llm-clearance. Returns a dict on a clean 200, or None to mean
    "proceed / fail-open" (404, 503, transport error, or malformed body).

    Latches _feature_disabled on 503.
    """
    global _feature_disabled

    url = f"{_base_url()}/llm-clearance"
    try:
        resp = requests.get(url, timeout=_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        _warn_once("timeout")
        return None
    except requests.exceptions.RequestException as e:
        _warn_once(f"network:{type(e).__name__}")
        return None

    if resp.status_code == 200:
        try:
            payload = resp.json()
        except ValueError:
            _warn_once("malformed_json")
            return None
        return payload if isinstance(payload, dict) else None

    if resp.status_code == 404:
        # Scope resolved but no clearance configured — proceed.
        return None
    if resp.status_code == 503:
        # Feature off — latch and proceed for the rest of the process.
        _feature_disabled = True
        return None

    # 5xx / other — fail-open with a one-shot warn.
    _warn_once(f"http_{resp.status_code}")
    return None


def check_and_pause() -> None:
    """Block while the orchestrator reports this agent is over a spend limit.

    Call BEFORE issuing an LLM provider request. Cheap on the hot path: a cleared
    verdict is cached for _CACHE_TTL_SEC. When blocked, sleeps (SIGTERM-aware via
    tf.sleep) in capped slices until the reported reset, re-checking each wake.
    Fail-open: any endpoint error / unreachable / feature-off ⇒ returns
    immediately (never wedges real work on an orchestrator hiccup).
    """
    global _last_clear_ts

    if _feature_disabled:
        return

    now = time.monotonic()
    if (now - _last_clear_ts) < _CACHE_TTL_SEC:
        return

    from .lifecycle import sleep as _tf_sleep

    while True:
        verdict = _fetch_clearance()

        # None ⇒ fail-open / feature-off / no limit configured. Treat as cleared
        # and cache so we don't hammer the endpoint while it's down or absent.
        if verdict is None:
            _last_clear_ts = time.monotonic()
            return

        if verdict.get("cleared"):
            _last_clear_ts = time.monotonic()
            return

        # Breached — pause until reset, then re-check (verdict NOT cached).
        resets_at = verdict.get("resets_at")
        scope = verdict.get("scope")
        scope_label = f" (scope: {scope})" if scope else ""
        log_warn(
            f"[cost_clearance] AI spend limit reached{scope_label} — pausing until "
            f"{resets_at or 'limit clears'}."
        )
        _tf_sleep(min(_seconds_until(resets_at), _MAX_PAUSE_SLICE_SEC))
