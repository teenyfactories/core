"""
tf.secrets() — read-only client for the orchestrator's in-built secrets store.

Calls the orchestrator's internal-only secrets endpoint at
`http://orchestrator:8998/secrets/<KEY>`. The endpoint is reachable only
from inside the private agent network — the orchestrator never publishes
8998 to the host, regardless of which deployment backend (compose,
kubernetes, ...) is provisioning the network. Trust is anchored on
private-network membership; the orchestrator resolves the caller's scope
from the source IP. We send X-Factory-Name + X-Agent-Name as
defence-in-depth so the orchestrator can sanity-check that the headers
agree with the network-derived scope.

Read-only. No `.set` / `.rotate` from agent code — admin UI handles writes.

Failure-mode policy (locked by tf-framework-architect):
    200 + {value}     → return value
    404               → silent fallthrough to os.getenv(KEY)
    503               → silent fallthrough + LATCH for the rest of the
                        process lifetime (feature-off signal; don't keep
                        round-tripping when it'll never return)
    5xx / network     → log_warn ONCE per (key,reason) per process,
                        fallthrough to os.getenv(KEY)
    timeout (2s)      → same as network error
    never raise

Usage:
    api_key = tf.secrets('ANTHROPIC_API_KEY')
    api_key = tf.secrets('ANTHROPIC_API_KEY', default='sk-test-fallback')
"""

import os
from urllib.parse import quote

import requests

from .config import FACTORY_NAME, AGENT_NAME
from .logging import log_debug, log_warn, log_error

_DEFAULT_BASE_URL = 'http://orchestrator:8998'
_TIMEOUT_SECONDS = 2.0

# Process-lifetime latch: once the orchestrator says 503 (feature off),
# every subsequent tf.secrets() call falls through to env without retrying.
# Restart the process to re-probe.
_feature_disabled = False

# Dedupe log_warn calls so a flapping orchestrator doesn't flood the log.
_warned = set()


def _base_url() -> str:
    return os.getenv('TF_SECRETS_URL', _DEFAULT_BASE_URL).rstrip('/')


def secrets(key_name: str, default=None):
    """Look up a secret by name; fall back to env var; finally `default`.

    See module docstring for the failure-mode policy.
    """
    global _feature_disabled

    if not key_name:
        return default

    # Process-lifetime feature-off latch.
    if _feature_disabled:
        return os.getenv(key_name) or default

    url = f'{_base_url()}/secrets/{quote(key_name, safe="")}'
    headers = {
        'X-Factory-Name': FACTORY_NAME or '',
        'X-Agent-Name':   AGENT_NAME or '',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT_SECONDS)
    except requests.exceptions.Timeout:
        _warn_once(key_name, 'timeout')
        return os.getenv(key_name) or default
    except requests.exceptions.RequestException as e:
        _warn_once(key_name, f'network:{type(e).__name__}')
        return os.getenv(key_name) or default

    if resp.status_code == 200:
        try:
            payload = resp.json()
        except ValueError:
            _warn_once(key_name, 'malformed_json')
            return os.getenv(key_name) or default
        value = payload.get('value')
        if isinstance(value, str) and value:
            return value
        return os.getenv(key_name) or default

    # 403 = orchestrator refused (no X-Factory-Name header / NetworkPolicy
    # gate / etc). 404 = scope resolved but no secret stored. Both fall back
    # to env-or-default from the caller's POV, but 403 is operationally
    # significant — it means the agent failed to authenticate to the secrets
    # endpoint and is now running blind. Log at ERROR so operators see it.
    if resp.status_code == 403:
        log_error(
            f"tf.secrets({key_name!r}): orchestrator rejected (403). "
            f"Falling back to env var / default. Check NetworkPolicy + "
            f"X-Factory-Name header wiring."
        )
        return os.getenv(key_name) or default
    if resp.status_code == 404:
        return os.getenv(key_name) or default

    if resp.status_code == 503:
        _feature_disabled = True
        return os.getenv(key_name) or default

    # 5xx or other: log once, fall back.
    _warn_once(key_name, f'http_{resp.status_code}')
    return os.getenv(key_name) or default


def _warn_once(key_name: str, reason: str) -> None:
    # Transport/HTTP failures are DEBUG, not WARN — they're expected in
    # standalone (no-orchestrator) mode and harmless in orchestrator-managed
    # mode when env-var fallback covers it. The final WARN-and-raise lives
    # upstream in config.require_api_key, which fires only when EVERY cascade
    # step (factory secret → global secret → orchestrator env → agent env)
    # came back empty.
    sig = (key_name, reason)
    if sig in _warned:
        return
    _warned.add(sig)
    log_debug(f'tf.secrets({key_name!r}) HTTP cascade unreachable ({reason}); falling back to env var')
