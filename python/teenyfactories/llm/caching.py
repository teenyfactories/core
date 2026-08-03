"""Prompt-caching helpers for the agentic loop.

Caching is ALWAYS ON (no opt-in). Two levers, applied where the provider supports
them:

- **Anthropic-direct** caches via explicit ``cache_control`` breakpoints: a static
  one on the system block (``cache_system_message``) plus a *rolling* one on the
  last message each turn (``mark_cache_tail``) so the growing tool-loop prefix
  caches turn-to-turn. Two breakpoints — well inside Anthropic's limit of four.
- **OpenRouter / OpenAI-compatible** backends prefix-cache automatically per upstream.
  The loop builds ONE client and reuses it every turn, so a caller's stable
  ``extra_body`` (e.g. ``{'provider': {'order': [...]}}``) rides every request
  unchanged — best-effort consistent routing, which is what lets the upstream cache
  bite. tf does NOT auto-discover or pin an upstream: langchain-openai strips
  OpenRouter's served-``provider`` field before it reaches the AIMessage, so it can't
  be read back without patching langchain (which we don't). A factory that wants hard
  stickiness sets its own ``provider.order``.
- Other providers (Google/Ollama/Azure Bedrock) have no message-layer breakpoint, so
  these are no-ops for them.
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


def mark_cache_tail(messages, provider: str):
    """Rolling cache breakpoint (Anthropic only): return the message list to invoke
    with, where the LAST message carries a ``cache_control: ephemeral`` marker so the
    whole accumulated tool-loop prefix caches turn-to-turn. Applied to a shallow copy
    of the tail message, so the persistent ``messages`` list stays marker-free (the
    marker must ride only the current request, not accumulate across turns).

    Non-Anthropic providers cache automatically / via routing — returned unchanged."""
    if provider != "anthropic" or not messages:
        return messages
    last = messages[-1]
    content = getattr(last, "content", None)
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    elif isinstance(content, list) and content:
        blocks = list(content)
        tail = dict(blocks[-1]) if isinstance(blocks[-1], dict) else {"type": "text", "text": str(blocks[-1])}
        tail["cache_control"] = {"type": "ephemeral"}
        blocks[-1] = tail
    else:
        return messages
    try:
        return messages[:-1] + [last.model_copy(update={"content": blocks})]
    except Exception as e:  # pragma: no cover - defensive
        log_debug(f"💬 anthropic tail cache_control unavailable: {e}")
        return messages


def bind_tools_cached(client, specs, provider: str):
    """Bind tools (the cacheable system+tools prefix is what makes prompt caching
    effective in the loop). Tool-spec-level cache hints are provider-layer work;
    no-op beyond bind_tools today."""
    return client.bind_tools(specs)
