"""Google Gemini LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider
from teenyfactories.logging import log_warn


class GoogleProvider(LLMProvider):
    """Google Gemini implementation of LLM provider"""

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ):
        """Get Google Gemini LLM client. Optional overrides for model + temperature + max_tokens.
        `extra_body` is ignored (OpenAI-compatible providers only) — logs a warning if passed.

        `max_tokens` (when set) caps output tokens via
        ChatGoogleGenerativeAI's `max_output_tokens` kwarg; when None nothing
        is passed and the provider default applies.
        """
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("langchain-google-genai not available - install with 'pip install langchain-google-genai'")

        if extra_body:
            log_warn("tf.llm().with_extra_body ignored for google — OpenAI-compatible providers only (openai/openrouter/digitalocean)")

        client_kwargs = {
            'google_api_key': config.require_api_key('google'),
            'model': model or config.require_llm_model(),
            'temperature': 0.3 if temperature is None else temperature,
        }
        if max_tokens is not None:
            client_kwargs['max_output_tokens'] = max_tokens

        return ChatGoogleGenerativeAI(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        """Get the Google model name"""
        return model or config.require_llm_model()
