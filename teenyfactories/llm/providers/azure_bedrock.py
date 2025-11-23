"""Azure Bedrock LLM provider implementation"""

import os
import urllib.parse
from teenyfactories.llm.base import LLMProvider


class AzureBedrockProvider(LLMProvider):
    """Azure Bedrock implementation of LLM provider (supports O3 models)"""

    def __init__(self):
        self.bedrock_url = os.getenv('AZURE_BEDROCK_LLM_URL')
        self.bedrock_key = os.getenv('AZURE_BEDROCK_LLM_KEY')

        if not self.bedrock_url or not self.bedrock_key:
            raise ValueError("Azure Bedrock requires AZURE_BEDROCK_LLM_URL and AZURE_BEDROCK_LLM_KEY environment variables")

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

    def get_client(self):
        """Get Azure Bedrock LLM client"""
        try:
            from langchain_openai import AzureChatOpenAI
        except ImportError:
            raise ImportError("langchain-openai not available - install with 'pip install langchain-openai'")

        # Create Azure OpenAI client using LangChain
        # Note: o3 models don't support temperature parameter
        client_kwargs = {
            'api_key': self.bedrock_key,
            'openai_api_version': self.azure_api_version,
            'azure_endpoint': self.azure_endpoint,
            'azure_deployment': self.azure_deployment,
        }

        # Handle temperature parameter based on model type
        if 'o3' in self.azure_deployment.lower():
            # o3 models don't support temperature - use raw OpenAI client wrapper
            from openai import AzureOpenAI
            from langchain_core.runnables import Runnable
            from langchain_core.messages import HumanMessage, AIMessage

            class O3AzureWrapper(Runnable):
                """Custom wrapper for o3 models that don't support temperature"""
                def __init__(self, azure_client, deployment):
                    self.client = azure_client
                    self.deployment = deployment
                    self.temperature = None  # For debug display

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
                    response = self.client.chat.completions.create(
                        model=self.deployment,
                        messages=openai_messages
                        # Deliberately omitting temperature for o3 models
                    )

                    # Return AIMessage for LangChain compatibility
                    return AIMessage(content=response.choices[0].message.content)

            # Create raw Azure OpenAI client
            raw_client = AzureOpenAI(
                api_key=self.bedrock_key,
                api_version=self.azure_api_version,
                azure_endpoint=self.azure_endpoint
            )

            return O3AzureWrapper(raw_client, self.azure_deployment)
        else:
            # Other models support temperature
            client_kwargs['temperature'] = 0.3
            return AzureChatOpenAI(**client_kwargs)

    def get_model_name(self) -> str:
        """Get the Azure Bedrock model name"""
        return self.azure_deployment
