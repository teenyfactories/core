"""Base LLM abstraction with multi-provider support"""

import os
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Type, TypeVar, Optional

# Pydantic Models for LLM Response Validation (lazy import)
try:
    from pydantic import BaseModel, ValidationError
    from langchain.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    T = TypeVar('T', bound=BaseModel)
except ImportError:
    # Fallback if packages not available
    BaseModel = object
    ValidationError = Exception
    PydanticOutputParser = None
    PromptTemplate = None
    T = TypeVar('T')

from teenyfactories.config import PROJECT_NAME
from teenyfactories.logging import log_info, log_error, log_debug, log_warn
from teenyfactories.utils import get_aest_now


# =============================================================================
# ABSTRACT BASE CLASS
# =============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers"""

    @abstractmethod
    def get_client(self):
        """Get the LLM client instance"""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the model name for this provider"""
        pass


# =============================================================================
# PUBLIC API FUNCTIONS
# =============================================================================

def get_llm_client(model_provider: Optional[str] = None):
    """
    Get an LLM client based on the provider

    Args:
        model_provider: Provider name ('openai', 'anthropic', 'google', 'ollama', 'azure_bedrock')
                       If None, uses DEFAULT_LLM_PROVIDER environment variable

    Returns:
        LLM client instance (LangChain compatible)

    Example:
        >>> client = get_llm_client('openai')
        >>> client = get_llm_client()  # Uses DEFAULT_LLM_PROVIDER
    """
    provider = model_provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai')

    if provider == "openai":
        from .providers.openai import OpenAIProvider
        return OpenAIProvider().get_client()

    elif provider == "anthropic":
        from .providers.anthropic import AnthropicProvider
        return AnthropicProvider().get_client()

    elif provider == "google":
        from .providers.google import GoogleProvider
        return GoogleProvider().get_client()

    elif provider == "ollama":
        from .providers.ollama import OllamaProvider
        return OllamaProvider().get_client()

    elif provider == "azure_bedrock":
        from .providers.azure_bedrock import AzureBedrockProvider
        return AzureBedrockProvider().get_client()

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _get_model_name(provider: Optional[str] = None) -> str:
    """
    Get the model name based on the provider

    Args:
        provider: Provider name

    Returns:
        Model name string
    """
    provider = provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai')

    if provider == "openai":
        from .providers.openai import OpenAIProvider
        return OpenAIProvider().get_model_name()

    elif provider == "anthropic":
        from .providers.anthropic import AnthropicProvider
        return AnthropicProvider().get_model_name()

    elif provider == "google":
        from .providers.google import GoogleProvider
        return GoogleProvider().get_model_name()

    elif provider == "ollama":
        from .providers.ollama import OllamaProvider
        return OllamaProvider().get_model_name()

    elif provider == "azure_bedrock":
        from .providers.azure_bedrock import AzureBedrockProvider
        return AzureBedrockProvider().get_model_name()

    else:
        return f"unknown-{provider}"


def clean_json_response(response_text: str) -> str:
    """
    Clean LLM response by extracting JSON content and removing markdown wrappers

    Args:
        response_text: Raw response text from LLM

    Returns:
        Cleaned JSON string

    Example:
        >>> raw = '```json\\n{"key": "value"}\\n```'
        >>> clean_json_response(raw)
        '{"key": "value"}'
    """
    # Remove markdown code blocks
    response_text = re.sub(r'^```(?:json)?\s*', '', response_text, flags=re.MULTILINE)
    response_text = re.sub(r'\s*```$', '', response_text, flags=re.MULTILINE)

    # Try to extract JSON from text that may have explanatory content
    # Look for the first occurrence of { and the last occurrence of }
    start_brace = response_text.find('{')
    if start_brace != -1:
        # Find the matching closing brace by counting braces
        brace_count = 0
        for i in range(start_brace, len(response_text)):
            if response_text[i] == '{':
                brace_count += 1
            elif response_text[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    # Found the complete JSON object
                    json_text = response_text[start_brace:i+1]
                    return json_text.strip()

    # If no complete JSON object found, return the cleaned text as-is
    return response_text.strip()


def call_llm(
    prompt_template,
    prompt_inputs,
    response_model: Type[T],
    model_provider: Optional[str] = None,
    context_info: Optional[str] = None,
    retry_attempt: Optional[int] = None
) -> T:
    """
    Call LLM with comprehensive logging and required pydantic parsing

    Args:
        prompt_template: LangChain PromptTemplate or template string
        prompt_inputs: Dictionary of inputs for the prompt template
        response_model: Pydantic model class for response validation
        model_provider: Optional provider name (defaults to DEFAULT_LLM_PROVIDER)
        context_info: Optional context information for logging
        retry_attempt: Optional retry attempt number for logging

    Returns:
        Parsed response as instance of response_model

    Raises:
        Exception: If LLM call fails or response validation fails

    Example:
        >>> from pydantic import BaseModel
        >>> from langchain_core.prompts import PromptTemplate
        >>>
        >>> class AnalysisResult(BaseModel):
        ...     summary: str
        ...     score: float
        >>>
        >>> template = PromptTemplate.from_template("Analyze: {text}")
        >>> result = call_llm(template, {"text": "sample"}, AnalysisResult)
        >>> print(result.summary)
    """
    start_time = time.time()
    success = False
    error_message = None
    response_text = ""
    token_info = {}
    parsed_response = None

    try:
        llm = get_llm_client(model_provider)

        # Set up pydantic parsing (now required)
        if PydanticOutputParser is None:
            raise ImportError("PydanticOutputParser not available - install langchain package")
        parser = PydanticOutputParser(pydantic_object=response_model)

        # Add format instructions to the prompt if not already present
        format_instructions = parser.get_format_instructions()
        if hasattr(prompt_template, 'template') and "{format_instructions}" in prompt_template.template:
            prompt_inputs["format_instructions"] = format_instructions
        elif hasattr(prompt_template, 'template'):
            # Append format instructions to the template
            new_template = prompt_template.template + "\n\n{format_instructions}"
            if PromptTemplate is None:
                raise ImportError("PromptTemplate not available - install langchain-core package")
            prompt_template = PromptTemplate.from_template(new_template)
            prompt_inputs["format_instructions"] = format_instructions

        # Create the chain
        chain = prompt_template | llm

        # Make the call
        log_info("💬 Calling LLM")
        result = chain.invoke(prompt_inputs)
        if hasattr(result, 'content'):
            response_text = result.content
        else:
            response_text = str(result)

        # Clean response text (remove markdown wrappers)
        response_text = clean_json_response(response_text)

        # Parse with pydantic (now always required)
        try:
            parsed_response = parser.parse(response_text)
            log_debug(f"✅ Successfully parsed response with {response_model.__name__}")
        except ValidationError as ve:
            log_warn(f"⚠️ Pydantic validation error: {ve}")
            log_info(f"🔍 Raw response text that failed validation: {response_text[:500]}...")
            # Try to extract JSON and parse again
            try:
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    log_info(f"🔍 Extracted JSON for retry: {json_str[:200]}...")
                    parsed_response = response_model.model_validate_json(json_str)
                    log_info(f"✅ Successfully parsed JSON extract with {response_model.__name__}")
                else:
                    log_warn(f"⚠️ No valid JSON found in response: {response_text[:200]}...")
                    raise ValidationError("No valid JSON found in response")
            except Exception as parse_err:
                log_error(f"❌ Failed to parse response: {parse_err}")
                log_info(f"🔍 Final failed response text: {response_text[:300]}...")
                # Raise error since pydantic parsing is now required
                raise Exception(f"Failed to parse LLM response with {response_model.__name__}: {parse_err}")

        # Extract token information if available
        if hasattr(result, 'usage_metadata') and result.usage_metadata is not None:
            usage = result.usage_metadata
            token_info = {
                'input_tokens': usage.get('input_tokens', 0),
                'output_tokens': usage.get('output_tokens', 0),
                'total_tokens': usage.get('total_tokens', 0)
            }

        success = True

    except Exception as e:
        error_message = str(e)
        log_error(f"❌ LLM call failed: {error_message}")

    finally:
        duration_ms = int((time.time() - start_time) * 1000)

        # Log the LLM usage (simplified - no file storage)
        try:
            usage_log = {
                'id': str(uuid.uuid4()),
                'timestamp': get_aest_now().isoformat(),
                'project': PROJECT_NAME,
                'provider': model_provider or os.getenv('DEFAULT_LLM_PROVIDER', 'openai'),
                'model': _get_model_name(model_provider),
                'temperature': 0.3,
                'context': context_info,
                'retry_attempt': retry_attempt,
                'duration_ms': duration_ms,
                'input_tokens': token_info.get('input_tokens'),
                'output_tokens': token_info.get('output_tokens'),
                'total_tokens': token_info.get('total_tokens'),
                'response_model': response_model.__name__ if response_model else None,
                'parsed_successfully': parsed_response is not None,
                'success': success,
                'error': error_message
            }

            # Log to debug
            log_debug(f"📊 LLM Usage: {usage_log.get('provider')}/{usage_log.get('model')} - {duration_ms}ms")

        except Exception as log_err:
            log_warn(f"⚠️ Failed to log LLM usage: {log_err}")

    if success:
        if parsed_response:
            return parsed_response
        else:
            raise Exception("Failed to parse LLM response - pydantic parsing is required")
    else:
        raise Exception(error_message)
