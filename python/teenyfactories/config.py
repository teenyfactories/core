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

Policy:
  • Mandatory values fail loud at use-site — no silent defaults.
  • The orchestrator-side compose file uses `${VAR:?required}` so missing
    values fail at compose-up too; this module is the runtime backstop
    when an agent runs outside the orchestrator's spawning path.

Resolution cascade:
  `get()` / `require()` resolve a value by asking the orchestrator's
  in-built secrets/runtime-var store FIRST (tf.secrets → :8998 managed
  table → its own env fallback), and only consult os.environ when the
  cascade returns nothing. This lets an operator set a GLOBAL in the UI
  env-var table (e.g. DEFAULT_LLM_PROVIDER=openrouter) and have running
  agents pick it up at call time — no pod restart, no static re-injection.

  The :8998 store is deny-by-default: only keys registered in
  runtimeVars.js's ORCHESTRATOR_RUNTIME_VARS whitelist resolve there; any
  other key 404s and silently falls through to os.environ. So per-container
  identity vars (FACTORY_NAME, AGENT_NAME, HOSTNAME/AGENT_ID, AGENT_SLUG)
  — never whitelisted — never depend on :8998.

  Import-time safety: the module-load reads below (identity vars, logging
  bootstrap, Postgres connection params) use `_env_only()` — a pure
  os.environ read with NO :8998 round-trip — so `import teenyfactories`
  never blocks on the secrets layer and there's no circular import
  (secrets.py imports FACTORY_NAME from this module). Only the on-demand
  getters cascade.

  Fail-open + cached: cascade reads are memoised per-process for
  _CASCADE_TTL_SEC so the hot path (config.get/require are called
  frequently, some at import) doesn't round-trip :8998 per call. If the
  cascade is unreachable / the feature is off, tf.secrets() already falls
  straight to os.environ and never blocks — we inherit that resilience.
"""

import logging
import os
import time

from dotenv import load_dotenv

# Load .env when running outside Docker (no-op inside containers — env is
# already populated by the orchestrator's container manager).
load_dotenv()


# ── Cascade resolution (secrets/runtime-var store → os.environ) ─────────────
#
# A cascade lookup is memoised for this long so config.get/require don't hit
# :8998 on every call. Mirrors secrets.py / cost_clearance.py TTL posture.
# Short enough that a global edited in the UI env-var table reaches agents
# within the window; long enough to keep the round-trip off the hot path.
_CASCADE_TTL_SEC = 45.0

# name -> (monotonic_ts, value_or_None). value is the cascade's answer for
# this key (managed table → secrets.py's own env fallback). None means the
# cascade had nothing AND its env fallback was empty too.
_cascade_cache: dict[str, tuple[float, str | None]] = {}


def _env_only(name: str) -> str | None:
    """Pure os.environ read (empty string treated as unset). NO :8998 call.

    Used for import-time reads (identity vars, logging, Postgres params) so
    `import teenyfactories` never depends on the secrets layer, and as the
    final fallback for the cascade getters."""
    val = os.environ.get(name)
    if val is None or val == "":
        return None
    return val


def _cascade(name: str) -> str | None:
    """Resolve a value via the secrets/runtime-var cascade, memoised.

    Returns the cascade's value (managed :8998 table → secrets.py env
    fallback) or None if it had nothing. Fail-open: any error inside
    tf.secrets() already degrades to an os.environ read, so this never
    raises and never blocks the agent.

    Empty/whitespace cache entries are stored as None so callers treat them
    as unset. The lazy import mirrors require_api_key — secrets.py imports
    FACTORY_NAME from this module, so a top-level import would be circular.
    """
    now = time.monotonic()
    hit = _cascade_cache.get(name)
    if hit is not None and (now - hit[0]) < _CASCADE_TTL_SEC:
        return hit[1]

    try:
        from teenyfactories.secrets import secrets as _secrets
        val = _secrets(name)
    except Exception:
        # secrets layer not importable yet / transport blew up in an
        # unexpected way → behave as "cascade had nothing", fall to env.
        val = None

    if val is not None and val == "":
        val = None
    _cascade_cache[name] = (now, val)
    return val


# ── Helpers ─────────────────────────────────────────────────────────────────


def get(name: str, default: str | None = None) -> str | None:
    """Read a config value with an optional default.

    Resolution order: secrets/runtime-var cascade (tf.secrets → :8998
    managed table → its own env fallback) → os.environ → default. Empty
    strings are treated as unset at every step. See module docstring for
    the cascade + caching + fail-open semantics."""
    val = _cascade(name)
    if val is not None:
        return val
    val = _env_only(name)
    if val is not None:
        return val
    return default


def require(name: str, hint: str | None = None) -> str:
    """Read a required config value; raise RuntimeError if unset/empty.

    Same cascade → os.environ resolution as `get`, but raises instead of
    returning a default when nothing resolves anywhere."""
    val = _cascade(name)
    if val is not None:
        return val
    val = _env_only(name)
    if val is not None:
        return val
    suffix = f" — {hint}" if hint else ""
    raise RuntimeError(
        f"Required environment variable {name} is not set{suffix}. "
        f"The orchestrator should have injected this (or registered it in "
        f"the env-var table); if you're running the agent outside the "
        f"orchestrator, set it in your .env."
    )


# ── Per-container identifiers (set by the orchestrator) ─────────────────────

# Set by orchestrator/backend/services/containerManager.js when spawning
# each agent container. FACTORY_NAME doubles as the NOTIFY channel prefix
# ({factory_name}.{collection}.{state}). The 'unknown' fallback only fires
# in dev runs outside the orchestrator.
#
# These four read via _env_only (direct os.environ, NO :8998 call): they are
# per-container identity, never registered in the runtime-var table, and they
# are read at module load — routing them through the cascade would (a) add a
# :8998 dependency to `import teenyfactories` and (b) risk a circular import,
# since secrets.py imports FACTORY_NAME from this module. They'd 404 in the
# cascade anyway. See module docstring "Import-time safety".
FACTORY_NAME = _env_only("FACTORY_NAME") or "unknown"
AGENT_NAME = _env_only("AGENT_NAME") or "unknown"

# AGENT_SLUG = the canonical, machine-stable identifier for this agent within
# its factory (factory.yml agents key). Set by the orchestrator alongside
# AGENT_NAME. Used as factory_logs.service_name so log queries stay stable
# when an agent's display name (AGENT_NAME) is edited. Empty string in dev
# runs that haven't injected it; the logger falls back to AGENT_NAME.
AGENT_SLUG = _env_only("AGENT_SLUG") or ""

# AGENT_ID = the full container hostname. Docker daemon sets HOSTNAME to the
# container ID at create; Kubernetes sets it to the pod name. Stored on
# factory_logs.container_id as the per-instance identifier so multiple
# replicas of the same AGENT_NAME stay distinguishable. Empty string when
# running outside a container (dev runs).
AGENT_ID = _env_only("HOSTNAME") or ""


# ── Logging ────────────────────────────────────────────────────────────────
#
# No threshold knob. Every tf.log_* call emits — to stdout AND, when a
# Postgres host is injected, to the factory_logs table. UI-side filtering
# (NodeLogsPanel) is the place to hide noisy levels at view-time.

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

if _env_only("POSTGRES_HOST"):
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
#
# Naming (2026-05-24, #159): POSTGRES_FACTORY_USER + POSTGRES_FACTORY_PASSWORD
# are canonical for agent containers. They map to a per-env LOGIN role
# (tf_<env>_factory) with reduced privileges — no admin schema access, RLS-
# fenced to this agent's FACTORY_NAME. POSTGRES_USER + POSTGRES_PASSWORD
# remain as transitional fallback for envs not yet re-keyed; agents fall
# back if FACTORY_* is unset.

# Connection-plane params read via _env_only (direct os.environ, NO :8998
# call). These are read at module load to open the ONE process-wide DB
# connection — and the cascade itself can't be consulted before that
# connection exists in any meaningful ordering. They are also the credentials
# the agent uses to reach Postgres; bootstrapping them from a network service
# that may itself need the DB would be circular. Keep them env-only.
POSTGRES_HOST = _env_only("POSTGRES_HOST") or "postgres"
POSTGRES_PORT = int(_env_only("POSTGRES_PORT") or "5432")
POSTGRES_DB = _env_only("POSTGRES_DB") or "teenyfactories"
POSTGRES_USER = _env_only("POSTGRES_FACTORY_USER") or _env_only("POSTGRES_USER") or "teenyfactories"
# No default — orchestrator always injects this. Agents started outside
# the orchestrator must set it explicitly.
POSTGRES_PASSWORD = _env_only("POSTGRES_FACTORY_PASSWORD") or _env_only("POSTGRES_PASSWORD")
if POSTGRES_PASSWORD is None and _env_only("POSTGRES_HOST"):
    raise RuntimeError(
        "Required environment variable POSTGRES_FACTORY_PASSWORD (or legacy "
        "POSTGRES_PASSWORD) is not set — orchestrator database password. The "
        "orchestrator should have injected this; if you're running the agent "
        "outside the orchestrator, set it in your .env."
    )


# ── Connection helper (single source of truth for psycopg2.connect) ────────
#
# This helper is called ONLY by `teenyfactories.db` — the process-wide
# shared-connection module. Every tf-core component (message queue,
# collections, claims, logging, usage recorder, breakpoints) rides that one
# connection via `db.get_connection()` / `db.cursor()`; nothing else may
# call this helper directly. It sets `app.factory_name` to FACTORY_NAME on
# the session so the RLS policies on factory_* tables (init_orchestrator.sql,
# #159) auto-fence reads/writes to this factory's rows only.
#
# Fails closed: if FACTORY_NAME is unset OR equal to the dev "unknown"
# sentinel, the SET is issued honestly (with that value) — the RLS policy
# then admits no rows (NULL = factory_name is NULL). One-time warn log so
# dev runs outside the orchestrator surface the cause quickly.
#
# Connection lifecycle: agents open ONE long-lived connection per process;
# no pool, no checkout/release. The SESSION-scoped SET survives until the
# connection drops; `db.get_connection()` reconnects through this helper,
# which re-SETs.
_warned_unknown_factory = False


def connect_postgres():
    """Open a psycopg2 connection scoped to this factory via RLS.

    AUTOCOMMIT mode + `SET app.factory_name = FACTORY_NAME` issued once
    on connect. Caller owns the connection lifecycle.
    """
    global _warned_unknown_factory
    import psycopg2
    import psycopg2.extensions

    # Self-labelling in pg_stat_activity: factory/agent@container. AGENT_ID
    # is the container hostname (docker container id / k8s pod name); dev
    # runs outside a container fall back to the pid. Postgres truncates
    # application_name at 63 bytes — acceptable.
    app_name = f"{FACTORY_NAME}/{AGENT_SLUG or AGENT_NAME}@{AGENT_ID or f'pid-{os.getpid()}'}"

    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        application_name=app_name,
    )
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    with conn.cursor() as cur:
        cur.execute("SET app.factory_name = %s", (FACTORY_NAME,))
    if FACTORY_NAME == "unknown" and not _warned_unknown_factory:
        logging.getLogger("teenyfactories").warning(
            "RLS scope is 'unknown' — set FACTORY_NAME to your factory's name "
            "to see real data (RLS policy fences unknown to zero rows)."
        )
        _warned_unknown_factory = True
    return conn


# ── LLM provider resolution (mandatory at use-site) ────────────────────────


def require_llm_provider() -> str:
    """Resolve the active LLM provider name. Raises if DEFAULT_LLM_PROVIDER
    is unset; agents never get a silent default."""
    return require(
        "DEFAULT_LLM_PROVIDER",
        "one of: openai, anthropic, google, ollama, azure_bedrock, digitalocean, openrouter",
    )


def require_llm_model() -> str:
    """Resolve the active LLM model name. Raises if DEFAULT_LLM_MODEL is unset."""
    return require("DEFAULT_LLM_MODEL", "specific model name for the chosen provider")


def require_embedding_provider() -> str:
    return require("DEFAULT_EMBEDDING_PROVIDER", "one of: openai, ollama, openrouter")


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
        "digitalocean": "DIGITALOCEAN_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(provider)
    if not var_name:
        raise RuntimeError(f"Unknown LLM provider '{provider}'")
    # Cascade via the shared memoised resolver (tf.secrets → :8998 → env),
    # NOT a second direct tf.secrets() call — `get`/`require` now cascade
    # too, so calling require() here would double round-trip the same key.
    # _cascade already covers the secrets path + its env fallback; only fall
    # to a raw env read + raise when the cascade came back empty.
    val = _cascade(var_name) or _env_only(var_name)
    if val:
        return val
    raise RuntimeError(
        f"Required environment variable {var_name} is not set — "
        f"required by DEFAULT_LLM_PROVIDER='{provider}'. The orchestrator "
        f"should have injected this (or registered it in the env-var table); "
        f"if you're running the agent outside the orchestrator, set it in "
        f"your .env."
    )


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
