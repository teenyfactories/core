"""Ollama LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None, temperature: Optional[float] = None):
        """Get Ollama LLM client. Optional overrides for model + temperature."""
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError:
            raise ImportError("langchain-community not available - install with 'pip install langchain-community'")

        return ChatOllama(
            model=model or config.require_llm_model(),
            base_url=config.get('OLLAMA_BASE_URL', config.OLLAMA_DEFAULT_BASE_URL),
            temperature=0.3 if temperature is None else temperature,
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
