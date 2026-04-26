"""Anthropic LLM provider implementation"""

import os
from typing import Optional
from teenyfactories.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Anthropic implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None):
        """Get Anthropic LLM client. Optional `model` overrides DEFAULT_LLM_MODEL."""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic not available - install with 'pip install langchain-anthropic'")

        return ChatAnthropic(
            anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
            model=model or os.getenv('DEFAULT_LLM_MODEL', 'claude-sonnet-4-20250514'),
            temperature=0.3
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or os.getenv('DEFAULT_LLM_MODEL', 'claude-sonnet-4-20250514')
