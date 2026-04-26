"""Ollama LLM provider implementation"""

import os
from typing import Optional
from teenyfactories.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None):
        """Get Ollama LLM client. Optional `model` overrides DEFAULT_LLM_MODEL."""
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError:
            raise ImportError("langchain-community not available - install with 'pip install langchain-community'")

        return ChatOllama(
            model=model or os.getenv('DEFAULT_LLM_MODEL', 'llama2'),
            base_url=os.getenv('OLLAMA_BASE_URL', 'http://host.docker.internal:11434'),
            temperature=0.3
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or os.getenv('DEFAULT_LLM_MODEL', 'llama2')
