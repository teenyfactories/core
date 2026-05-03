"""Anthropic LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class AnthropicProvider(LLMProvider):
    """Anthropic implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None, temperature: Optional[float] = None):
        """Get Anthropic LLM client. Optional overrides for model + temperature."""
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("langchain-anthropic not available - install with 'pip install langchain-anthropic'")

        return ChatAnthropic(
            anthropic_api_key=config.require_api_key('anthropic'),
            model=model or config.require_llm_model(),
            temperature=0.3 if temperature is None else temperature,
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
