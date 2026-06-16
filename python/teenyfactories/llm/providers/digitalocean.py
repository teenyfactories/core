"""DigitalOcean Gradient AI LLM provider implementation.

DigitalOcean's Gradient AI inference endpoint is OpenAI-API-compatible
(bearer key, identical chat/completions/embeddings request+response shape),
so this provider piggy-backs on `langchain_openai.ChatOpenAI` with a custom
`openai_api_base` pointed at DO's inference URL.

Defaults:
  base_url:  https://inference.do-ai.run/v1     (override: DIGITALOCEAN_INFERENCE_URL)
  api_key:   DIGITALOCEAN_API_KEY               (resolved via config.require_api_key)
  model:     caller-supplied via DEFAULT_LLM_MODEL or per-call `model=` kwarg
             (DO model names, e.g. 'llama3.3-70b-instruct',
             'anthropic-claude-opus-4.7')

Selected when DEFAULT_LLM_PROVIDER='digitalocean'. The router in
`teenyfactories/llm/base.py` binds the string 'digitalocean' to this class
via the `_PROVIDERS` dict — that string IS the canonical provider name,
never derived from the Python class identifier.

Reasoning-class Anthropic models proxied via DO (e.g. `anthropic-claude-opus-4.7`,
`anthropic-claude-opus-4.8`) reject the `temperature` request kwarg upstream
and the rejection is forwarded as a 400 by DO. We detect by model-ID substring
and omit the kwarg in those cases — same pattern as the native Anthropic
provider.
"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


# Canonical provider-name string. Kept here as a class attribute so any future
# code that needs to stamp the provider identity onto a log row, usage record,
# or error message can read it as data (not derive it from `__class__.__name__`,
# which would be fragile under any future rename or bundling step).
_PROVIDER_NAME = 'digitalocean'

# Default Gradient AI inference endpoint. Override per-deployment with
# DIGITALOCEAN_INFERENCE_URL (e.g. for a region-pinned or private endpoint).
DIGITALOCEAN_DEFAULT_BASE_URL = 'https://inference.do-ai.run/v1'

# Model-ID substrings that reject the `temperature` kwarg when reached via DO.
# DO's Anthropic-backed model names use dots (e.g. 'anthropic-claude-opus-4.7')
# rather than the dashes used in native Anthropic IDs ('claude-opus-4-7'), so
# we list both forms. Match is case-insensitive substring.
DIGITALOCEAN_NO_TEMPERATURE_MODELS = (
    'claude-opus-4-7',
    'claude-opus-4-8',
    'claude-opus-4.7',
    'claude-opus-4.8',
)


def _model_rejects_temperature(model_id: str) -> bool:
    """True if the given DO model ID is in the reject-temperature set."""
    if not model_id:
        return False
    m = model_id.lower()
    return any(token in m for token in DIGITALOCEAN_NO_TEMPERATURE_MODELS)


class DigitalOceanProvider(LLMProvider):
    """DigitalOcean Gradient AI implementation (OpenAI-compatible API).

    Anthropic reasoning-class models proxied through DO (Opus 4.7+) reject
    the `temperature` kwarg; in that case the client is constructed without
    it. Every other model behaves as today.
    """

    providerName = _PROVIDER_NAME  # class-level literal — minify/rename-safe
    provider_name = _PROVIDER_NAME  # snake_case alias for Python-side reads

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """Get a DigitalOcean Gradient AI LLM client. Optional overrides for model + temperature + max_tokens.

        If the resolved model is a DO-proxied Anthropic reasoning model
        (Opus 4.7+), the temperature kwarg is omitted — DO forwards a 400
        from Anthropic otherwise.

        `max_tokens` (when set) caps output tokens via the OpenAI-compatible
        `max_tokens` kwarg; when None nothing is passed and the upstream
        default applies.
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError(
                "langchain-openai not available - install with 'pip install langchain-openai' "
                "(DigitalOcean Gradient AI uses the OpenAI-compatible client)"
            )

        resolved_model = model or config.require_llm_model()

        client_kwargs = {
            'openai_api_key': config.require_api_key('digitalocean'),
            'openai_api_base': config.get(
                'DIGITALOCEAN_INFERENCE_URL', DIGITALOCEAN_DEFAULT_BASE_URL
            ),
            'model_name': resolved_model,
        }

        if not _model_rejects_temperature(resolved_model):
            client_kwargs['temperature'] = 0.3 if temperature is None else temperature

        if max_tokens is not None:
            client_kwargs['max_tokens'] = max_tokens

        return ChatOpenAI(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
