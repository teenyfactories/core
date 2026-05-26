"""
Usage recorder — internal-only.

Single write path for LLM/embedding usage rows on factory_logs from the
tf-side. Wraps the SECURITY DEFINER function `record_llm_usage()` (callable
by tf_factory_user; granted in init_orchestrator.sql).

Design mirrors orchestrator/backend/services/usageRecorder.js:
  - Failures NEVER break the underlying LLM/embedding call. Every write is
    wrapped in try/except with a warn-level log.
  - This is internal-only: factory authors don't call it directly. The
    framework records on their behalf from tf.call_llm and tf.embed.

Connection model: a dedicated, lazy, autocommit psycopg2 connection — same
shape as PostgresLogHandler. Kept off the message_queue provider's
transactional cursor so a usage write can never disrupt pub/sub state.
"""

from typing import Optional

from . import config
from .logging import log_warn

_PREVIEW_LEN = 80
_VALID_KINDS = ('llm', 'embedding')

_conn = None
_cursor = None


def _preview_of(text) -> Optional[str]:
    """Truncate to _PREVIEW_LEN chars, NULL-safe."""
    if text is None:
        return None
    s = text if isinstance(text, str) else str(text)
    return s[:_PREVIEW_LEN] if len(s) > _PREVIEW_LEN else s


def _get_cursor():
    """Lazy autocommit connection for usage writes. Returns None on failure."""
    global _conn, _cursor
    if _cursor is not None:
        return _cursor
    if not config.get("POSTGRES_HOST"):
        return None
    try:
        # config.connect_postgres() handles psycopg2.connect + isolation level
        # + RLS scope SET. Single source of truth.
        _conn = config.connect_postgres()
        _cursor = _conn.cursor()
        return _cursor
    except Exception as e:
        log_warn(f"[usage_recorder] connect failed: {e}")
        _conn = None
        _cursor = None
        return None


def log_usage(
    *,
    call_kind: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_creation_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: Optional[int] = None,
    request_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    prompt_preview: Optional[str] = None,
    factory_name: Optional[str] = None,
    service_name: Optional[str] = None,
    user_id: str = 'system',
) -> Optional[int]:
    """
    Persist one usage row via record_llm_usage(). Internal-only.

    Required: call_kind ('llm' | 'embedding'), provider, model.
    factory_name and service_name default to config.FACTORY_NAME / config.AGENT_NAME.

    Returns the inserted row id, or None on validation/write failure.
    Failures are logged at warn level and swallowed — usage recording must
    never break the underlying LLM call.
    """
    if call_kind not in _VALID_KINDS:
        log_warn(f"[usage_recorder] invalid call_kind={call_kind!r}; skipping")
        return None
    if not provider or not model:
        log_warn("[usage_recorder] missing provider or model; skipping")
        return None

    fname = factory_name or config.FACTORY_NAME
    sname = service_name or config.AGENT_NAME
    if not fname or not sname:
        log_warn("[usage_recorder] missing factory_name or service_name; skipping")
        return None

    cursor = _get_cursor()
    if cursor is None:
        return None

    try:
        cursor.execute(
            """SELECT public.record_llm_usage(
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s
            )""",
            (
                fname,
                sname,
                user_id or 'system',
                call_kind,
                provider,
                model,
                int(input_tokens or 0),
                int(cached_input_tokens or 0),
                int(cache_creation_tokens or 0),
                int(output_tokens or 0),
                None if latency_ms is None else int(latency_ms),
                request_id,
                chat_id,
                _preview_of(prompt_preview),
            ),
        )
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception as e:
        log_warn(f"[usage_recorder] write failed: {e}")
        # Reset connection on failure — next call will reconnect.
        global _conn, _cursor
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn = None
        _cursor = None
        return None
