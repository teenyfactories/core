"""Azure Bedrock LLM provider implementation"""

import urllib.parse
from typing import Optional
from teenyfactories import config
from teenyfactories.llm.base import LLMProvider


class AzureBedrockProvider(LLMProvider):
    """Azure Bedrock implementation of LLM provider (supports O3 models)"""

    def __init__(self):
        self.bedrock_url = config.require('AZURE_BEDROCK_LLM_URL', 'required by DEFAULT_LLM_PROVIDER=azure_bedrock')
        self.bedrock_key = config.require('AZURE_BEDROCK_LLM_KEY', 'required by DEFAULT_LLM_PROVIDER=azure_bedrock')

        # Parse the URL to extract components
        # URL format: https://resource.openai.azure.com/openai/deployments/deployment/chat/completions?api-version=version
        parsed = urllib.parse.urlparse(self.bedrock_url)

        self.azure_endpoint = f"{parsed.scheme}://{parsed.netloc}/"

        # Extract deployment name from path
        path_parts = parsed.path.split('/')
        if 'deployments' in path_parts:
            deployment_idx = path_parts.index('deployments') + 1
            self.azure_deployment = path_parts[deployment_idx] if deployment_idx < len(path_parts) else 'o3-mini'
        else:
            self.azure_deployment = 'o3-mini'

        # Extract API version from query params
        query_params = urllib.parse.parse_qs(parsed.query)
        self.azure_api_version = query_params.get('api-version', ['2025-01-01-preview'])[0]

    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """Get Azure Bedrock LLM client. Optional overrides for model + temperature + max_tokens.
        o3 models silently ignore the temperature override (the SDK rejects it).

        `max_tokens` (when set) caps output tokens. For standard models it maps
        to AzureChatOpenAI's `max_tokens`; for o3 reasoning models it maps to
        the raw OpenAI `max_completion_tokens` kwarg (o3 rejects `max_tokens`).
        When None nothing is passed and the provider default applies."""
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        deployment = model or self.azure_deployment

        # Create Azure OpenAI client using LangChain
        # Note: o3 models don't support temperature parameter
        client_kwargs = {
            'api_key': self.bedrock_key,
            'openai_api_version': self.azure_api_version,
            'azure_endpoint': self.azure_endpoint,
            'azure_deployment': deployment,
        }

        # Handle temperature parameter based on model type
        if 'o3' in deployment.lower():
            # o3 models don't support temperature - use raw OpenAI client wrapper
            from openai import AzureOpenAI
            from langchain_core.runnables import Runnable
            from langchain_core.messages import HumanMessage, AIMessage

            class O3AzureWrapper(Runnable):
                """Custom wrapper for o3 models that don't support temperature"""
                def __init__(self, azure_client, deployment, max_completion_tokens=None):
                    self.client = azure_client
                    self.deployment = deployment
                    self.temperature = None  # For debug display
                    # o3 reasoning models reject `max_tokens`; the output cap is
                    # passed as `max_completion_tokens`. None = provider default.
                    self.max_completion_tokens = max_completion_tokens

                def invoke(self, input, config=None):
                    # Handle different input types from LangChain chains
                    if hasattr(input, 'to_messages'):
                        # PromptValue input
                        messages = input.to_messages()
                        openai_messages = []
                        for msg in messages:
                            if hasattr(msg, 'content'):
                                role = "user" if isinstance(msg, HumanMessage) else "assistant"
                                openai_messages.append({"role": role, "content": msg.content})
                    elif isinstance(input, str):
                        # Direct string input
                        openai_messages = [{"role": "user", "content": input}]
                    else:
                        # Fallback for other input types
                        openai_messages = [{"role": "user", "content": str(input)}]

                    # Make API call without temperature parameter
                    create_kwargs = {
                        'model': self.deployment,
                        'messages': openai_messages,
                        # Deliberately omitting temperature for o3 models
                    }
                    if self.max_completion_tokens is not None:
                        create_kwargs['max_completion_tokens'] = self.max_completion_tokens
                    response = self.client.chat.completions.create(**create_kwargs)

                    # Return AIMessage for LangChain compatibility
                    return AIMessage(content=response.choices[0].message.content)

            # Create raw Azure OpenAI client
            raw_client = AzureOpenAI(
                api_key=self.bedrock_key,
                api_version=self.azure_api_version,
                azure_endpoint=self.azure_endpoint
            )

            return O3AzureWrapper(raw_client, deployment, max_completion_tokens=max_tokens)
        else:
            # Other models support temperature
            client_kwargs['temperature'] = 0.3 if temperature is None else temperature
            if max_tokens is not None:
                client_kwargs['max_tokens'] = max_tokens
            return AzureChatOpenAI(**client_kwargs)

    def get_model_name(self, model: Optional[str] = None) -> str:
        """Get the Azure Bedrock model name"""
        return model or self.azure_deployment
