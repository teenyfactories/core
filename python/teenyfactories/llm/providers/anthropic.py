"""Anthropic LLM provider implementation"""

import os
from teenyfactories.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Anthropic implementation of LLM provider"""

    def get_client(self):
        """Get Anthropic LLM client"""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic not available - install with 'pip install langchain-anthropic'")

        return ChatAnthropic(
            anthropic_api_key=os.getenv('ANTHROPIC_API_KEY'),
            model=os.getenv('DEFAULT_LLM_MODEL', 'claude-sonnet-4-20250514'),
            temperature=0.3
        )

    def get_model_name(self) -> str:
        return os.getenv('DEFAULT_LLM_MODEL', 'claude-sonnet-4-20250514')
