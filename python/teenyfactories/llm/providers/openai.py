"""OpenAI LLM provider implementation"""

import os
from teenyfactories.llm.base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI implementation of LLM provider"""

    def get_client(self):
        """Get OpenAI LLM client"""
        try:
            from langchain_openai import ChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        return ChatOpenAI(
            openai_api_key=os.getenv('OPENAI_API_KEY'),
            model_name=os.getenv('DEFAULT_LLM_MODEL', 'gpt-4o-mini'),
            temperature=0.3
        )

    def get_model_name(self) -> str:
        return os.getenv('DEFAULT_LLM_MODEL', 'gpt-4o-mini')
