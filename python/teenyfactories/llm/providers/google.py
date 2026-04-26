"""Google Gemini LLM provider implementation"""

import os
from typing import Optional
from teenyfactories.llm.base import LLMProvider


class GoogleProvider(LLMProvider):
    """Google Gemini implementation of LLM provider"""

    def get_client(self, model: Optional[str] = None):
        """Get Google Gemini LLM client. Optional `model` overrides GOOGLE_MODEL."""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("langchain-google-genai not available - install with 'pip install langchain-google-genai'")

        return ChatGoogleGenerativeAI(
            google_api_key=os.getenv('GOOGLE_API_KEY'),
            model=model or os.getenv('GOOGLE_MODEL', 'gemini-pro'),
            temperature=0.3
        )

    def get_model_name(self, model: Optional[str] = None) -> str:
        """Get the Google model name"""
        return model or os.getenv('GOOGLE_MODEL', 'gemini-pro')
