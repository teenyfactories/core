"""Base LLM abstraction with multi-provider support.

Public surface (consumed by factory agents):

    get_llm_client(provider, model)   — returns a LangChain-compatible client
    clean_json_response(text)         — strips markdown fences + extracts JSON
    call_llm(template, inputs, ...)   — full prompt → parsed-pydantic pipeline
    LLMProvider                       — ABC for provider implementations

`call_llm` is the primary entry point. It glues together six concerns —
client lookup, prompt augmentation with format-instructions, chain invocation,
response cleaning, pydantic parsing (with regex-extraction fallback), and
token-usage telemetry — and forwards the result into `factory_logs` via
`usage_recorder.log_usage()`. Each concern lives in its own helper below
so the orchestration in `call_llm` stays readable.
"""

import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Type, TypeVar, Optional

# Pydantic / LangChain — lazy import so test suites that don't need LLM
# can still import this module.
try:
    from pydantic import BaseModel, ValidationError
    from langchain_core.output_parsers import PydanticOutputParser
    from langchain_core.prompts import PromptTemplate
    T = TypeVar('T', bound=BaseModel)
except ImportError:
    BaseModel = object
    ValidationError = Exception
    PydanticOutputParser = None
    PromptTemplate = None
    T = TypeVar('T')

from teenyfactories import config
from teenyfactories.logging import log_info, log_error, log_debug, log_warn


# =============================================================================
# Provider registry — collapses the two if/elif ladders into one table
# =============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def get_client(self, model: Optional[str] = None):
        """Return a LangChain-compatible client. `model` overrides DEFAULT_LLM_MODEL."""

    @abstractmethod
    def get_model_name(self, model: Optional[str] = None) -> str:
        """Return the resolved model name for this provider."""


def _load_openai():
    from .providers.openai import OpenAIProvider
    return OpenAIProvider()


def _load_anthropic():
    from .providers.anthropic import AnthropicProvider
    return AnthropicProvider()


def _load_google():
    from .providers.google import GoogleProvider
    return GoogleProvider()


def _load_ollama():
    from .providers.ollama import OllamaProvider
    return OllamaProvider()


def _load_azure_bedrock():
    from .providers.azure_bedrock import AzureBedrockProvider
    return AzureBedrockProvider()


def _load_digitalocean():
    from .providers.digitalocean import DigitalOceanProvider
    return DigitalOceanProvider()


# Single registry — drives both `get_llm_client` and `_get_model_name`.
# Each value is a zero-arg callable returning a fresh provider instance;
# the actual provider module is imported lazily on first use so importing
# `teenyfactories.llm` doesn't pull in every SDK.
_PROVIDERS = {
    'openai':        _load_openai,
    'anthropic':     _load_anthropic,
    'google':        _load_google,
    'ollama':        _load_ollama,
    'azure_bedrock': _load_azure_bedrock,
    'digitalocean':  _load_digitalocean,
}


def _resolve_provider(name: Optional[str]) -> str:
    return name or config.require_llm_provider()


def _get_provider_instance(name: Optional[str]) -> LLMProvider:
    provider = _resolve_provider(name)
    loader = _PROVIDERS.get(provider)
    if loader is None:
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return loader()


# =============================================================================
# Public: client + model resolution
# =============================================================================

def get_llm_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
):
    """
    Return a LangChain-compatible LLM client.

    Args:
        provider:    'openai' / 'anthropic' / 'google' / 'ollama' /
                     'azure_bedrock' / 'digitalocean'. Defaults to
                     DEFAULT_LLM_PROVIDER.
        model:       Optional model override (e.g. 'claude-haiku-4-5-20251001').
                     Defaults to DEFAULT_LLM_MODEL.
        temperature: Optional sampling temperature override. Defaults to the
                     provider's built-in default (0.3 for the chat-style
                     models; ignored on Azure o3).
    """
    return _get_provider_instance(provider).get_client(
        model=model, temperature=temperature
    )


def _get_model_name(provider: Optional[str] = None, model: Optional[str] = None) -> str:
    if model:
        return model
    try:
        return _get_provider_instance(provider).get_model_name()
    except ValueError:
        return f"unknown-{_resolve_provider(provider)}"


# =============================================================================
# Public: response cleaning
# =============================================================================

def clean_json_response(response_text: str) -> str:
    """
    Strip markdown fences and extract the first balanced JSON object.

    Returns the cleaned text. If no `{...}` block is found, returns the
    fence-stripped text as-is so the caller can decide what to do.
    """
    response_text = re.sub(r'^```(?:json)?\s*', '', response_text, flags=re.MULTILINE)
    response_text = re.sub(r'\s*```$', '', response_text, flags=re.MULTILINE)

    start = response_text.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(response_text)):
            if response_text[i] == '{':
                depth += 1
            elif response_text[i] == '}':
                depth -= 1
                if depth == 0:
                    return response_text[start:i + 1].strip()

    return response_text.strip()


# =============================================================================
# call_llm helpers — one concern per function
# =============================================================================

def _prepare_prompt(prompt_template, prompt_inputs, response_model):
    """
    Inject pydantic format-instructions into the template + inputs.

    Mutates `prompt_inputs` in place to add `format_instructions`. Returns
    `(prompt_template, parser)` — `prompt_template` may be a new
    PromptTemplate if the original didn't already declare the slot.
    """
    if PydanticOutputParser is None:
        raise ImportError("PydanticOutputParser not available — install langchain")

    parser = PydanticOutputParser(pydantic_object=response_model)
    instructions = parser.get_format_instructions()

    if hasattr(prompt_template, 'template'):
        if "{format_instructions}" in prompt_template.template:
            prompt_inputs["format_instructions"] = instructions
        else:
            if PromptTemplate is None:
                raise ImportError("PromptTemplate not available — install langchain-core")
            prompt_template = PromptTemplate.from_template(
                prompt_template.template + "\n\n{format_instructions}"
            )
            prompt_inputs["format_instructions"] = instructions

    return prompt_template, parser


def _invoke_chain(prompt_template, llm, prompt_inputs):
    """Run `template | llm` and return (raw_result, response_text)."""
    chain = prompt_template | llm
    log_info("💬 Calling LLM")
    result = chain.invoke(prompt_inputs)
    text = result.content if hasattr(result, 'content') else str(result)
    return result, text


def _parse_response(response_text: str, parser, response_model):
    """
    Parse cleaned response into the response_model. Falls back to a regex
    extract + `model_validate_json` if the strict parse fails.
    """
    try:
        return parser.parse(response_text)
    except ValidationError as ve:
        log_warn(f"⚠️ Pydantic validation error: {ve}")
        log_info(f"🔍 Raw response text that failed validation: {response_text[:500]}...")

    match = re.search(r'\{.*\}', response_text, re.DOTALL)
    if not match:
        raise Exception(
            f"Failed to parse LLM response with {response_model.__name__}: "
            f"no JSON object found"
        )

    try:
        log_info(f"🔍 Extracted JSON for retry: {match.group(0)[:200]}...")
        return response_model.model_validate_json(match.group(0))
    except Exception as parse_err:
        log_error(f"❌ Failed to parse response: {parse_err}")
        log_info(f"🔍 Final failed response text: {response_text[:300]}...")
        raise Exception(
            f"Failed to parse LLM response with {response_model.__name__}: {parse_err}"
        )


def _extract_token_info(result) -> dict:
    """Pull token counts off the LangChain result, including cache details."""
    usage = getattr(result, 'usage_metadata', None)
    if not usage:
        return {}
    # Anthropic exposes cache_read + cache_creation; OpenAI only cache_read.
    details = usage.get('input_token_details') or {}
    return {
        'input_tokens':          usage.get('input_tokens', 0),
        'output_tokens':         usage.get('output_tokens', 0),
        'total_tokens':          usage.get('total_tokens', 0),
        'cached_input_tokens':   details.get('cache_read', 0) or 0,
        'cache_creation_tokens': details.get('cache_creation', 0) or 0,
    }


def _build_prompt_preview(prompt_template, prompt_inputs) -> str:
    """Best-effort render of the resolved prompt for the usage log."""
    try:
        if hasattr(prompt_template, 'format'):
            return prompt_template.format(**(prompt_inputs or {}))
        return str(prompt_template)
    except Exception:
        return str(prompt_inputs)[:200]


def _record_call_usage(*, provider, model, token_info, duration_ms,
                       prompt_template, prompt_inputs):
    """Persist the usage row via the SECURITY DEFINER record_llm_usage()."""
    try:
        from teenyfactories.usage_recorder import log_usage
        log_usage(
            call_kind='llm',
            provider=provider,
            model=model,
            input_tokens=token_info.get('input_tokens', 0) or 0,
            cached_input_tokens=token_info.get('cached_input_tokens', 0) or 0,
            cache_creation_tokens=token_info.get('cache_creation_tokens', 0) or 0,
            output_tokens=token_info.get('output_tokens', 0) or 0,
            latency_ms=duration_ms,
            request_id=str(uuid.uuid4()),
            chat_id=None,
            prompt_preview=_build_prompt_preview(prompt_template, prompt_inputs),
        )
    except Exception as usage_err:
        # log_usage already swallows internally; this only fires if the
        # import itself blows up.
        log_warn(f"⚠️ usage_recorder unavailable: {usage_err}")


# =============================================================================
# Public: call_llm
# =============================================================================

def call_llm(
    prompt_template,
    prompt_inputs,
    response_model: Optional[Type[T]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
):
    """
    Call the LLM, optionally parse the response into `response_model`, log usage.

    Args:
        prompt_template: LangChain PromptTemplate (or string-templated).
        prompt_inputs:   Dict of values for the template.
        response_model:  Optional pydantic class. If provided, the response is
                         validated against it and a typed instance is returned.
                         If None, the cleaned response text is returned as a
                         string (no JSON parsing, no fence cleanup beyond
                         markdown-stripping).
        provider:        Override DEFAULT_LLM_PROVIDER for this call.
        model:           Override DEFAULT_LLM_MODEL for this call.
        temperature:     Override the provider's default temperature for this
                         call. Passed through to the client constructor — never
                         mutates a shared client instance.

    Returns:
        Instance of `response_model` if one was given; otherwise the raw
        response text (str).

    Raises:
        Exception: any failure in client construction, invocation, or parsing.
                   Usage is still recorded in the finally block.
    """
    start_time = time.time()
    success = False
    error_message = None
    token_info: dict = {}
    result_value = None
    used_provider = _resolve_provider(provider)
    used_model = _get_model_name(provider, model=model)

    try:
        llm = get_llm_client(provider, model=model, temperature=temperature)

        if response_model is not None:
            prompt_template, parser = _prepare_prompt(
                prompt_template, prompt_inputs, response_model
            )
        else:
            parser = None

        raw_result, response_text = _invoke_chain(prompt_template, llm, prompt_inputs)

        if response_model is not None:
            response_text = clean_json_response(response_text)
            result_value = _parse_response(response_text, parser, response_model)
            log_debug(f"✅ Successfully parsed response with {response_model.__name__}")
        else:
            result_value = response_text

        token_info = _extract_token_info(raw_result)
        success = True

    except Exception as e:
        error_message = str(e)
        log_error(f"❌ LLM call failed: {error_message}")

    finally:
        duration_ms = int((time.time() - start_time) * 1000)
        log_debug(f"📊 LLM Usage: {used_provider}/{used_model} - {duration_ms}ms")
        _record_call_usage(
            provider=used_provider,
            model=used_model,
            token_info=token_info,
            duration_ms=duration_ms,
            prompt_template=prompt_template,
            prompt_inputs=prompt_inputs,
        )

    if success:
        return result_value
    raise Exception(error_message or "call_llm: produced no result")
