"""OpenAI LLM provider implementation"""

from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None, temperature: Optional[float] = None):
        """Get OpenAI LLM client. Optional overrides for model + temperature."""
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        return ChatOpenAI(
            openai_api_key=config.require_api_key('openai'),
            model_name=model or config.require_llm_model(),
            temperature=0.3 if temperature is None else temperature,
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        return model or config.require_llm_model()
