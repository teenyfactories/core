"""Ollama LLM provider implementation"""

import os
from teenyfactories.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama implementation of LLM provider"""

    def get_client(self):
        """Get Ollama LLM client"""
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError:
            raise ImportError("langchain-community not available - install with 'pip install langchain-community'")

        return ChatOllama(
            model=os.getenv('OLLAMA_MODEL', 'gpt-oss:20b'),
            base_url=os.getenv('OLLAMA_BASE_URL', 'http://host.docker.internal:11434'),
            temperature=0.3
        )

    def get_model_name(self) -> str:
        """Get the Ollama model name"""
        return os.getenv('OLLAMA_MODEL', 'gpt-oss:20b')
