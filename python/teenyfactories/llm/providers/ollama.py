"""Ollama LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama implementation of LLM provider"""

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """Get Ollama LLM client. Optional overrides for model + temperature + max_tokens.

        `max_tokens` (when set) caps output tokens via ChatOllama's
        `num_predict` kwarg (Ollama's name for the output-token limit); when
        None nothing is passed and Ollama's default applies.
        """
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError:
            raise ImportError("langchain-community not available - install with 'pip install langchain-community'")

        client_kwargs = {
            'model': model or config.require_llm_model(),
            'base_url': config.get('OLLAMA_BASE_URL', config.OLLAMA_DEFAULT_BASE_URL),
            'temperature': 0.3 if temperature is None else temperature,
        }
        if max_tokens is not None:
            client_kwargs['num_predict'] = max_tokens

        return ChatOllama(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
