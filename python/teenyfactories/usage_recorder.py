"""Usage recorder — internal-only.

Single write path for LLM/embedding usage rows on `factory_ai_usage` from the
tf-side. Wraps the SECURITY DEFINER function `public.record_ai_usage()`.

Cost is NOT computed tf-side. This recorder stores usage VERBATIM — the
orchestrator computes USD cost at READ time from the `raw` JSONB blob (which
carries token counts AND OpenRouter's reported per-generation cost). There is
no `cost_usd` / `cost_source` here anymore. Token counts are not separate
columns either — they live inside `raw` (e.g. raw.usage_metadata.input_tokens).

RAW IS VERBATIM — NEVER NORMALIZE TOKEN COUNTS HERE.
`raw` stores the provider's usage/response metadata exactly as returned, under
provider-specific paths (tf-side: `raw.usage_metadata.*`). Token counts are
normalized across provider shapes at READ time by the orchestrator's usage
endpoints (routes/adminRoutes.js `tokExpr`), NOT at write. This recorder must
not flatten provider tokens into top-level `raw.input_tokens` keys — those flat
paths exist only as a legacy read fallback for pre-verbatim rows. The ONLY
non-provider fields folded in here are app annotations with no column home
(latency_ms below; prompt_preview / pricing_version on the backend writer).

Design:
  • Failures NEVER break the underlying LLM/embedding call. Every write is
    wrapped in try/except with a warn-level log.
  • Internal-only: factory authors don't call it directly. The framework
    records on their behalf from tf.call_llm and tf.embed.

Connection model: rides the process-wide shared connection
(`teenyfactories.db`) with a fresh cursor per write. Single-statement
AUTOCOMMIT, so sharing cannot disrupt pub/sub (LISTEN/NOTIFY) state.
"""

import json
from typing import Optional

from . import config, db
from .logging import log_warn

_VALID_KINDS = ("llm", "embedding")


def log_usage(
    *,
    call_kind: str,
    provider: str,
    model: str,
    raw: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    request_id: Optional[str] = None,
    chat_id: Optional[str] = None,
    factory_name: Optional[str] = None,
    service_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Optional[int]:
    """Persist one usage row via record_ai_usage(). Internal-only.

    Required: call_kind ('llm' | 'embedding'), provider, model.
    factory_name / service_name default to config.FACTORY_NAME / AGENT_NAME.

    No cost is recorded here — the orchestrator computes USD cost at read time
    from `raw`. raw is the JSON-safe provider metadata blob (carries token
    counts + response metadata + any provider-reported cost); latency_ms, if
    supplied, is folded into raw so no telemetry is lost now that the table has
    no latency column.

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

    if not config.get("POSTGRES_HOST"):
        return None

    # Fold latency into raw so the metric survives the table's column drop.
    raw_payload = dict(raw) if isinstance(raw, dict) else ({"raw": raw} if raw is not None else {})
    if latency_ms is not None:
        raw_payload["latency_ms"] = int(latency_ms)
    raw_json = json.dumps(raw_payload, default=str) if raw_payload else None

    try:
        with db.cursor() as cursor:
            cursor.execute(
                """SELECT public.record_ai_usage(
                    %s, %s, %s, %s, %s,
                    %s, %s::jsonb, %s, %s, %s
                )""",
                (
                    fname,  # p_factory_name
                    sname,  # p_service_name
                    user_id,  # p_user_id (NULL ok)
                    call_kind,  # p_call_kind
                    provider,  # p_provider
                    model,  # p_model
                    raw_json,  # p_raw
                    request_id,  # p_request_id
                    chat_id,  # p_chat_id
                    None if latency_ms is None else int(latency_ms),  # p_latency_ms
                ),
            )
            row = cursor.fetchone()
            return row[0] if row else None
    except Exception as e:
        # Connection-class errors invalidate the shared connection (next
        # caller reconnects); SQL errors leave it alone.
        db.invalidate_if_dead(e)
        log_warn(f"[usage_recorder] write failed: {e}")
        return None
