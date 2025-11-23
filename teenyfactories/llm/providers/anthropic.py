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
            model=os.getenv('ANTHROPIC_MODEL', 'claude-3-sonnet-20240229'),
            temperature=0.3
        )

    def get_model_name(self) -> str:
        """Get the Anthropic model name"""
        return os.getenv('ANTHROPIC_MODEL', 'claude-3-sonnet-20240229')
