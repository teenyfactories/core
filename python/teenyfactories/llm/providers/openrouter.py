"""OpenRouter LLM provider implementation.

OpenRouter is an aggregator that exposes hundreds of upstream models behind a
single OpenAI-compatible HTTP API (bearer key, identical chat/completions
request+response shape), so this provider piggy-backs on
`langchain_openai.ChatOpenAI` with `openai_api_base` pointed at OpenRouter.

Defaults:
  base_url:  https://openrouter.ai/api/v1        (override: OPENROUTER_INFERENCE_URL)
  api_key:   OPENROUTER_API_KEY                  (resolved via config.require_api_key)
  model:     caller-supplied via DEFAULT_LLM_MODEL or per-call `model=` kwarg.
             OpenRouter model strings are provider-prefixed, e.g.
             'anthropic/claude-3-opus', 'openai/gpt-4o', 'google/gemini-pro'.

Selected when DEFAULT_LLM_PROVIDER='openrouter'. The router in
`teenyfactories/llm/base.py` binds the string 'openrouter' to this class
via the `_PROVIDERS` dict — that string IS the canonical provider name,
never derived from the Python class identifier.

Reasoning-class lockout: no models reject `temperature` at this layer today.
The `OPENROUTER_NO_TEMPERATURE_MODELS` tuple is left empty and the
`_model_rejects_temperature` helper is wired up so a future reasoning model
can be added by appending one substring to the tuple — same pattern as the
native Anthropic + DigitalOcean providers.
"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


# Canonical provider-name string. Kept here as a class attribute so any future
# code that needs to stamp the provider identity onto a log row, usage record,
# or error message can read it as data (not derive it from `__class__.__name__`,
# which would be fragile under any future rename or bundling step).
_PROVIDER_NAME = 'openrouter'

# Default OpenRouter inference endpoint. Override per-deployment with
# OPENROUTER_INFERENCE_URL (e.g. for a self-hosted proxy or alt-region edge).
OPENROUTER_DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'

# Model-ID substrings that reject the `temperature` kwarg when reached via
# OpenRouter. Empty today — populate when a routed reasoning model starts
# returning HTTP 400 `temperature is deprecated for this model`. Match is
# case-insensitive substring against the full OpenRouter model string
# (e.g. 'anthropic/claude-opus-4.7').
OPENROUTER_NO_TEMPERATURE_MODELS: tuple[str, ...] = ()


def _model_rejects_temperature(model_id: str) -> bool:
    """True if the given OpenRouter model ID is in the reject-temperature set."""
    if not model_id:
        return False
    m = model_id.lower()
    return any(token in m for token in OPENROUTER_NO_TEMPERATURE_MODELS)


class OpenRouterProvider(LLMProvider):
    """OpenRouter implementation (OpenAI-compatible aggregator API).

    Model strings are provider-prefixed, e.g. 'anthropic/claude-3-opus',
    'openai/gpt-4o', 'google/gemini-pro'. The `/` is significant — it tells
    OpenRouter which upstream to route to.
    """

    providerName = _PROVIDER_NAME  # class-level literal — minify/rename-safe
    provider_name = _PROVIDER_NAME  # snake_case alias for Python-side reads

    def get_client(self, model: Optional[str] = None, temperature: Optional[float] = None):
        """Get an OpenRouter LLM client. Optional overrides for model + temperature.

        If the resolved model is in `OPENROUTER_NO_TEMPERATURE_MODELS`, the
        temperature kwarg is omitted — OpenRouter forwards a 400 from the
        upstream provider otherwise.
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai not available - install with 'pip install langchain-openai' "
                "(OpenRouter uses the OpenAI-compatible client)"
            )

        resolved_model = model or config.require_llm_model()

        client_kwargs = {
            'openai_api_key': config.require_api_key('openrouter'),
            'openai_api_base': config.get(
                'OPENROUTER_INFERENCE_URL', OPENROUTER_DEFAULT_BASE_URL
            ),
            'model_name': resolved_model,
        }

        if not _model_rejects_temperature(resolved_model):
            client_kwargs['temperature'] = 0.3 if temperature is None else temperature

        return ChatOpenAI(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
