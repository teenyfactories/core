"""OpenAI LLM provider implementation"""

import os
from typing import Optional
from teenyfactories.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None):
        """Get OpenAI LLM client. Optional `model` overrides DEFAULT_LLM_MODEL."""
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        return ChatOpenAI(
            openai_api_key=os.getenv('OPENAI_API_KEY'),
            model_name=model or os.getenv('DEFAULT_LLM_MODEL', 'gpt-4o-mini'),
            temperature=0.3
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or os.getenv('DEFAULT_LLM_MODEL', 'gpt-4o-mini')
