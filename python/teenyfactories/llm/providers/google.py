"""Google Gemini LLM provider implementation"""

import os
from teenyfactories.llm.base import LLMProvider


class GoogleProvider(LLMProvider):
    """Google Gemini implementation of LLM provider"""

    def get_client(self):
        """Get Google Gemini LLM client"""
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("langchain-google-genai not available - install with 'pip install langchain-google-genai'")

        return ChatGoogleGenerativeAI(
            google_api_key=os.getenv('GOOGLE_API_KEY'),
            model=os.getenv('GOOGLE_MODEL', 'gemini-pro'),
            temperature=0.3
        )

    def get_model_name(self) -> str:
        """Get the Google model name"""
        return os.getenv('GOOGLE_MODEL', 'gemini-pro')
