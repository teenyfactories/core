"""
Configuration and environment-variable management for teenyfactories.

Single source of truth for env-var reads inside the framework. Agents
themselves never call os.getenv directly — they get their values from
the orchestrator's container injection (POSTGRES_*, FACTORY_NAME,
AGENT_NAME, etc.) or from the tf API which reads them via this module.

Two access patterns:
  • Constants for values that have sensible defaults (POSTGRES_HOST,
    POSTGRES_DB) — read once at import time.
  • `require(name)` raises a clear RuntimeError if the variable is unset
    or empty. Use this for credentials and other "must be set" values
    instead of letting None propagate to a downstream SDK error.

Policy (set with the user 2026-04-28):
  • Mandatory values fail loud at use-site — no silent defaults.
  • The orchestrator-side compose file uses `${VAR:?required}` so missing
    values fail at compose-up too; this module is the runtime backstop
    when an agent runs outside the orchestrator's spawning path.
"""

import logging
import os

from dotenv import load_dotenv

# Load .env when running outside Docker (no-op inside containers — env is
# already populated by the orchestrator's container manager).
load_dotenv()


# ── Helpers ─────────────────────────────────────────────────────────────────


def get(name: str, default: str | None = None) -> str | None:
    """Read an environment variable with an optional default. Empty strings
    are treated as unset."""
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val


def require(name: str, hint: str | None = None) -> str:
    """Read an environment variable; raise RuntimeError if unset/empty."""
    val = os.environ.get(name)
    if val is None or val == "":
        suffix = f" — {hint}" if hint else ""
        raise RuntimeError(
            f"Required environment variable {name} is not set{suffix}. "
            f"The orchestrator should have injected this; if you're running "
            f"the agent outside the orchestrator, set it in your .env."
        )
    return val


# ── Per-container identifiers (set by the orchestrator) ─────────────────────

# Set by orchestrator/backend/services/containerManager.js when spawning
# each agent container. FACTORY_NAME doubles as the NOTIFY channel prefix
# ({factory_name}.{collection}.{state}). The 'unknown' fallback only fires
# in dev runs outside the orchestrator.
FACTORY_NAME = get("FACTORY_NAME", "unknown")
AGENT_NAME = get("AGENT_NAME", "unknown")

# AGENT_ID = the full container hostname. Docker daemon sets HOSTNAME to the
# container ID at create; Kubernetes sets it to the pod name. Stored on
# factory_logs.container_id as the per-instance identifier so multiple
# replicas of the same AGENT_NAME stay distinguishable. Empty string when
# running outside a container (dev runs).
AGENT_ID = get("HOSTNAME", "")


# ── Logging ────────────────────────────────────────────────────────────────
#
# No threshold knob. Every tf.log_* call emits — to stdout AND, when a
# Postgres host is injected, to the factory_logs table. UI-side filtering
# (NodeLogsPanel) is the place to hide noisy levels at view-time.

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

if get("POSTGRES_HOST"):
    try:
        from .logging.logger import PostgresLogHandler  # noqa: WPS433 — local import to break cycle

        _pg_handler = PostgresLogHandler()
        _pg_handler.setLevel(logging.DEBUG)
        logging.getLogger("teenyfactories").addHandler(_pg_handler)
    except Exception:
        # Don't block agent startup if the handler can't initialise — the
        # stdout handler still works.
        pass


# ── Postgres connection (factory data plane + message queue) ───────────────

POSTGRES_HOST = get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(get("POSTGRES_PORT", "5432") or "5432")
POSTGRES_DB = get("POSTGRES_DB", "teenyfactories")
POSTGRES_USER = get("POSTGRES_USER", "teenyfactories")
# No default — orchestrator always injects this. Agents started outside
# the orchestrator must set it explicitly.
POSTGRES_PASSWORD = require("POSTGRES_PASSWORD", "orchestrator database password") if get("POSTGRES_HOST") else None


# ── LLM provider resolution (mandatory at use-site) ────────────────────────


def require_llm_provider() -> str:
    """Resolve the active LLM provider name. Raises if DEFAULT_LLM_PROVIDER
    is unset; agents never get a silent default."""
    return require(
        "DEFAULT_LLM_PROVIDER",
        "one of: openai, anthropic, google, ollama, azure_bedrock",
    )


def require_llm_model() -> str:
    """Resolve the active LLM model name. Raises if DEFAULT_LLM_MODEL is unset."""
    return require("DEFAULT_LLM_MODEL", "specific model name for the chosen provider")


def require_embedding_provider() -> str:
    return require("DEFAULT_EMBEDDING_PROVIDER", "one of: openai, ollama")


def require_embedding_model() -> str:
    return require("DEFAULT_EMBEDDING_MODEL", "specific embedding model name")


def require_api_key(provider: str) -> str:
    """Resolve the API key (or base URL) for a given provider. Tries the
    in-built secrets store first (tf.secrets) — that itself falls back to
    env vars when the secrets feature is off — and only raises if no value
    is found anywhere. The same key name (e.g. ANTHROPIC_API_KEY) is used
    in both stores; no rename map needed."""
    var_name = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "ollama": "OLLAMA_BASE_URL",
        "azure_bedrock": "AZURE_BEDROCK_LLM_KEY",
    }.get(provider)
    if not var_name:
        raise RuntimeError(f"Unknown LLM provider '{provider}'")
    # Local import to avoid a circular import at module load (secrets.py
    # imports FACTORY_NAME from config).
    from teenyfactories.secrets import secrets as _secrets
    val = _secrets(var_name)
    if val:
        return val
    return require(var_name, f"required by DEFAULT_LLM_PROVIDER='{provider}'")


# Default Ollama base URL — picked to match what works inside Docker
# containers (host.docker.internal resolves to the Docker host on macOS,
# Windows, and Linux with --add-host=host.docker.internal:host-gateway).
#
# LEGACY: this default is a Docker-Desktop-ism and will not resolve under
# the kubernetes backend. Decoupling is intentionally deferred to sub-project
# B PR 3 (the k8s backend ships an alternative — likely a configurable
# OLLAMA_BASE_URL with no implicit default, or a per-backend default
# supplied by the orchestrator). Do NOT change this default in isolation;
# every Docker-based deployment depends on it today.
OLLAMA_DEFAULT_BASE_URL = "http://host.docker.internal:11434"
