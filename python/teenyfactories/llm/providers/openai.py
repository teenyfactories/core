"""OpenAI LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLM provider"""

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """Get OpenAI LLM client. Optional overrides for model + temperature + max_tokens.

        `max_tokens` (when set) caps output tokens via ChatOpenAI's `max_tokens`
        kwarg; when None nothing is passed and the provider default applies.
        """
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        client_kwargs = {
            'openai_api_key': config.require_api_key('openai'),
            'model_name': model or config.require_llm_model(),
            'temperature': 0.3 if temperature is None else temperature,
        }
        if max_tokens is not None:
            client_kwargs['max_tokens'] = max_tokens

        return ChatOpenAI(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
