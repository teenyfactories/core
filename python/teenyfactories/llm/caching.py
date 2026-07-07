"""Prompt-caching helpers for the agentic loop (Phase 3).

Caching is ALWAYS ON (no opt-in). What's enforceable without a provider-layer
change is applied here; the OpenRouter within-run provider-stickiness needs the
provider's ``get_client`` to accept an ``extra_body`` override (so we can inject
``provider.order`` + ``require_parameters`` mid-loop) — until that lands,
``StickyPin`` OBSERVES the served upstream (for logging) but does not yet pin.
The loop is correct either way; this only affects cache-hit rate on OpenRouter.

Anthropic-direct caches immediately via a system-block ``cache_control``, which
IS doable at the message layer and is applied here.
"""

from teenyfactories.logging import log_debug


def cache_system_message(system_msg, provider: str):
    """Mark the (stable) system prefix cacheable where the provider supports an
    explicit breakpoint. Anthropic: ``cache_control: ephemeral`` on the system
    content block. Others: returned unchanged (OpenAI/OpenRouter prefix-cache is
    automatic / handled via routing)."""
    if provider == "anthropic":
        try:
            from langchain_core.messages import SystemMessage

            return SystemMessage(
                content=[{"type": "text", "text": system_msg.content, "cache_control": {"type": "ephemeral"}}]
            )
        except Exception as e:  # pragma: no cover - defensive
            log_debug(f"💬 anthropic system cache_control unavailable: {e}")
    return system_msg


def bind_tools_cached(client, specs, provider: str):
    """Bind tools (the cacheable system+tools prefix is what makes prompt caching
    effective in the loop). Tool-spec-level cache hints are provider-layer work;
    no-op beyond bind_tools today."""
    return client.bind_tools(specs)


class StickyPin:
    """Within-run OpenRouter provider stickiness (best-effort).

    OpenRouter routes each call independently, defeating DeepSeek prefix-caching.
    The fix: pin turns 2..N to the upstream that served turn 1. That requires
    injecting ``extra_body={'provider': {'order': [served], 'allow_fallbacks': True}}``
    when rebuilding the client — which needs the provider's ``get_client`` to
    accept an ``extra_body`` override (TODO: provider-layer hook). Until then this
    captures the served provider for visibility and ``maybe_rebind`` is a no-op.
    """

    def __init__(self, provider: str):
        self._enabled = provider == "openrouter"
        self._served = None

    def observe(self, raw: dict):
        """Capture the upstream provider that served the turn (best-effort —
        OpenRouter exposes it on response_metadata.provider)."""
        if not self._enabled or self._served is not None:
            return
        served = (raw or {}).get("response_metadata", {}).get("provider")
        if served:
            self._served = served
            log_debug(f"💬 within-run sticky target = OpenRouter upstream '{served}' (pinning is a provider-layer TODO)")

    def maybe_rebind(self, client, specs, bound, provider: str):
        """Return the client to use for this turn. TODO: once get_client accepts
        extra_body, rebind to pin self._served (+ require_parameters) on turns 2+.
        For now, returns the already-bound client unchanged."""
        return bound
