"""
tf.secrets() — read-only client for the orchestrator's in-built secrets store.

Calls the orchestrator's internal-only secrets endpoint at
`http://orchestrator:8998/secrets/<KEY>`. The endpoint is reachable only
from inside the docker network — compose never publishes 8998 to the host.
Trust is anchored on docker network membership; the orchestrator resolves
the caller's scope from the source IP. We send X-Factory-Name +
X-Agent-Name as defence-in-depth so the orchestrator can sanity-check that
the headers agree with the network-derived scope.

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
from .logging import log_warn

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

    if resp.status_code == 404:
        return os.getenv(key_name) or default

    if resp.status_code == 503:
        _feature_disabled = True
        return os.getenv(key_name) or default

    # 5xx or other: log once, fall back.
    _warn_once(key_name, f'http_{resp.status_code}')
    return os.getenv(key_name) or default


def _warn_once(key_name: str, reason: str) -> None:
    sig = (key_name, reason)
    if sig in _warned:
        return
    _warned.add(sig)
    log_warn(f'tf.secrets({key_name!r}) failed ({reason}); falling back to env var')
