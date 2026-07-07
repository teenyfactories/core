"""Anthropic LLM provider implementation.

Newer Anthropic reasoning-class models reject the `temperature` request kwarg
outright (HTTP 400, `temperature is deprecated for this model`). These models
sample deterministically server-side; the parameter is not silently ignored,
it is rejected. We detect by model-ID substring and build the client without
the temperature kwarg in that case.

The reject-temperature set is matched as data (literal substrings in the model
ID string), never via class/identifier introspection — consistent with the
project's minify-safe-names discipline.
"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider
from teenyfactories.logging import log_warn


# Model-ID substrings whose presence means the model rejects `temperature`.
# Match is case-insensitive substring against the resolved model name.
# Confirmed: claude-opus-4-7 (HTTP 400 on temperature).
# Anticipated same lockout: opus-4-8 and any subsequent reasoning-class release.
# When Anthropic ships a new reasoning model, add its identifying substring here.
ANTHROPIC_NO_TEMPERATURE_MODELS = (
    'claude-opus-4-7',
    'claude-opus-4-8',
)


def _model_rejects_temperature(model_id: str) -> bool:
    """True if the given Anthropic model ID is in the reject-temperature set."""
    if not model_id:
        return False
    m = model_id.lower()
    return any(token in m for token in ANTHROPIC_NO_TEMPERATURE_MODELS)


class AnthropicProvider(LLMProvider):
    """Anthropic implementation of LLM provider.

    Reasoning-class models (Opus 4.7+) reject the `temperature` kwarg.
    For those models the client is constructed without `temperature`; for
    every other Anthropic model the existing default (0.3) or caller override
    applies as before.
    """

    providerName = 'anthropic'   # class-level literal — minify/rename-safe
    provider_name = 'anthropic'  # snake_case alias for Python-side reads

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ):
        """Get Anthropic LLM client. Optional overrides for model + temperature + max_tokens.
        `extra_body` is ignored (OpenAI-compatible providers only) — logs a warning if passed.

        If the resolved model is in `ANTHROPIC_NO_TEMPERATURE_MODELS`, the
        temperature kwarg is omitted (Anthropic returns 400 otherwise). The
        caller's `temperature` argument is silently dropped in that case — the
        model does not support it.

        `max_tokens` (when set) caps output tokens via ChatAnthropic's
        `max_tokens` kwarg; when None nothing is passed and ChatAnthropic's
        default (1024) applies — so long structured responses should pass a
        larger value to avoid truncation.
        """
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic not available - install with 'pip install langchain-anthropic'")

        if extra_body:
            log_warn("tf.llm().with_extra_body ignored for anthropic — OpenAI-compatible providers only (openai/openrouter/digitalocean)")

        resolved_model = model or config.require_llm_model()

        client_kwargs = {
            'anthropic_api_key': config.require_api_key('anthropic'),
            'model': resolved_model,
        }

        if not _model_rejects_temperature(resolved_model):
            client_kwargs['temperature'] = 0.3 if temperature is None else temperature

        if max_tokens is not None:
            client_kwargs['max_tokens'] = max_tokens

        return ChatAnthropic(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
