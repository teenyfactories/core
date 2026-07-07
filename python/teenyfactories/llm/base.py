"""Base LLM abstraction with multi-provider support.

Public surface (consumed by factory agents):

    get_llm_client(provider, model)   — returns a LangChain-compatible client
    clean_json_response(text)         — strips markdown fences + extracts JSON
    call_llm(template, inputs, ...)   — full prompt → parsed-pydantic pipeline
    LLMProvider                       — ABC for provider implementations

`call_llm` is the primary entry point. It glues together client lookup, a
spend-limit clearance check (the orchestrator gates the call over :8998 BEFORE
the provider request — see `cost_clearance.py`), prompt augmentation with
format-instructions, chain invocation, response cleaning, pydantic parsing
(with regex-extraction fallback), and token-usage telemetry — forwarding the
result into `factory_ai_usage` via `usage_recorder.log_usage()`. Each concern
lives in its own helper below so the orchestration in `call_llm` stays readable.

Cost is NOT computed here. The verbatim provider usage metadata (including
OpenRouter's reported per-generation cost, captured via the provider's
`extra_body` usage flag) is stored on the usage row's `raw` blob; the
orchestrator computes USD cost from it at READ time.
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

    T = TypeVar("T", bound=BaseModel)
except ImportError:
    BaseModel = object
    ValidationError = Exception
    PydanticOutputParser = None
    PromptTemplate = None
    T = TypeVar("T")

from teenyfactories import config
from teenyfactories.logging import log_error, log_debug, log_warn

# =============================================================================
# Provider registry — collapses the two if/elif ladders into one table
# =============================================================================


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def get_client(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra_body: Optional[dict] = None,
    ):
        """Return a LangChain-compatible client. `model` overrides DEFAULT_LLM_MODEL.

        `temperature` and `max_tokens`, when set, are threaded into the client
        constructor per provider (never mutating a shared client). `max_tokens`
        caps OUTPUT tokens; when None the provider/langchain default applies and
        nothing is passed to the client.

        `extra_body`, when set, is a dict of extra attributes merged into the
        request body. Honoured only by the OpenAI-compatible providers
        (openai / openrouter / digitalocean) — forwarded verbatim via
        ChatOpenAI's `extra_body` (e.g. OpenRouter provider-routing prefs,
        top_p, seed). Providers on non-OpenAI SDKs (anthropic / google / ollama
        / azure_bedrock) log a warning and ignore it.
        """

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


def _load_openrouter():
    from .providers.openrouter import OpenRouterProvider

    return OpenRouterProvider()


# Single registry — drives both `get_llm_client` and `_get_model_name`.
# Each value is a zero-arg callable returning a fresh provider instance;
# the actual provider module is imported lazily on first use so importing
# `teenyfactories.llm` doesn't pull in every SDK.
_PROVIDERS = {
    "openai": _load_openai,
    "anthropic": _load_anthropic,
    "google": _load_google,
    "ollama": _load_ollama,
    "azure_bedrock": _load_azure_bedrock,
    "digitalocean": _load_digitalocean,
    "openrouter": _load_openrouter,
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
    max_tokens: Optional[int] = None,
    extra_body: Optional[dict] = None,
):
    """
    Return a LangChain-compatible LLM client.

    Args:
        provider:    'openai' / 'anthropic' / 'google' / 'ollama' /
                     'azure_bedrock' / 'digitalocean' / 'openrouter'.
                     Defaults to DEFAULT_LLM_PROVIDER.
        model:       Optional model override (e.g. 'claude-haiku-4-5-20251001').
                     Defaults to DEFAULT_LLM_MODEL.
        temperature: Optional sampling temperature override. Defaults to the
                     provider's built-in default (0.3 for the chat-style
                     models; ignored on Azure o3).
        max_tokens:  Optional cap on OUTPUT tokens. None (default) passes
                     nothing to the client, so the provider/langchain default
                     applies (e.g. ChatAnthropic's 1024). Mapped to each
                     provider's native kwarg.
        extra_body:  Optional dict of extra request-body attributes, merged in
                     by OpenAI-compatible providers only (openai / openrouter /
                     digitalocean). Ignored-with-warning on other providers.
    """
    return _get_provider_instance(provider).get_client(
        model=model, temperature=temperature, max_tokens=max_tokens, extra_body=extra_body
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
    response_text = re.sub(r"^```(?:json)?\s*", "", response_text, flags=re.MULTILINE)
    response_text = re.sub(r"\s*```$", "", response_text, flags=re.MULTILINE)

    start = response_text.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(response_text)):
            if response_text[i] == "{":
                depth += 1
            elif response_text[i] == "}":
                depth -= 1
                if depth == 0:
                    return response_text[start : i + 1].strip()

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

    if hasattr(prompt_template, "template"):
        if "{format_instructions}" in prompt_template.template:
            prompt_inputs["format_instructions"] = instructions
        else:
            if PromptTemplate is None:
                raise ImportError("PromptTemplate not available — install langchain-core")
            prompt_template = PromptTemplate.from_template(prompt_template.template + "\n\n{format_instructions}")
            prompt_inputs["format_instructions"] = instructions

    return prompt_template, parser


def _invoke_chain(prompt_template, llm, prompt_inputs):
    """Run `template | llm` and return (raw_result, response_text)."""
    chain = prompt_template | llm
    log_debug("💬 Calling LLM")
    result = chain.invoke(prompt_inputs)
    text = result.content if hasattr(result, "content") else str(result)
    return result, text


def _parse_response(response_text: str, parser, response_model):
    """
    Parse cleaned response into the response_model. Falls back to a regex
    extract + `model_validate_json` if the strict parse fails.
    """
    try:
        return parser.parse(response_text)
    except ValidationError as ve:
        log_debug(f"💬 Pydantic validation error (will retry via regex extract): {ve}")
        log_debug(f"💬 LLM raw response that failed strict parse: {response_text[:500]}")

    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if not match:
        raise Exception(f"Failed to parse LLM response with {response_model.__name__}: " f"no JSON object found")

    try:
        log_debug(f"💬 Extracted JSON from LLM result: {match.group(0)[:200]}")
        return response_model.model_validate_json(match.group(0))
    except Exception as parse_err:
        log_error(f"💬 Failed to parse LLM response: {parse_err} (response: {response_text[:300]})")
        raise Exception(f"Failed to parse LLM response with {response_model.__name__}: {parse_err}")


def _json_safe(obj):
    """Best-effort coerce a provider metadata blob into JSON-serializable form.

    LangChain usage/response metadata is normally plain dicts/lists/scalars,
    but some providers tuck in non-serializable objects. We round-trip through
    json with a str() fallback default so the raw blob always stores cleanly.
    """
    import json as _json

    try:
        return _json.loads(_json.dumps(obj, default=str))
    except Exception:
        try:
            return _json.loads(_json.dumps(str(obj)))
        except Exception:
            return None


def _extract_token_info(result) -> dict:
    """Pull the verbatim provider metadata off the LangChain result.

    Returns a dict with a single key:
      • 'raw' — JSON-safe copy of the verbatim provider usage + response
                metadata, stored on the usage row's `raw` JSONB.

    RAW IS VERBATIM. The 'raw' blob keeps usage_metadata + response_metadata
    exactly as langchain returned them (just made JSON-safe). Token counts and
    OpenRouter's per-generation `cost` ride along inside those blobs untouched —
    the orchestrator extracts and normalizes counts AND computes USD cost from
    `raw` at READ time. Do NOT re-shape, flatten, or price provider tokens here;
    if a provider exposes new usage fields, they pass through verbatim.
    """
    usage = getattr(result, "usage_metadata", None)
    rmeta = getattr(result, "response_metadata", None)

    # Raw blob: keep both the structured usage_metadata and the provider's
    # response_metadata (model snapshot id, finish_reason, system_fingerprint,
    # OpenRouter's per-generation cost, etc.). Verbatim, just made JSON-safe.
    raw = _json_safe(
        {
            "usage_metadata": usage,
            "response_metadata": rmeta,
        }
    )
    return {"raw": raw}


def _meta_from_raw(raw, provider, model, latency_ms) -> dict:
    """Assemble the returnable `meta` dict (a READ-VIEW over the verbatim `raw`
    blob — it does NOT reshape the stored raw). All fields are best-effort and
    may be None: `cost` is OpenRouter-only; `usage`/`finish_reason` shapes are
    provider-dependent. Consumed by tf.llm().ask_with_meta / tf.embed().with_meta.

      meta = {provider, model, cost, finish_reason, latency_ms, usage, raw}
      usage = verbatim LangChain usage_metadata
              (input_tokens/output_tokens/total_tokens/input_token_details{...}/...)
    """
    raw = raw or {}
    usage = raw.get("usage_metadata") or {}
    rmeta = raw.get("response_metadata") or {}
    token_usage = rmeta.get("token_usage") or {}
    return {
        "provider": provider,
        "model": rmeta.get("model_name") or model,
        "cost": token_usage.get("cost"),  # OpenRouter reports this; None elsewhere
        "finish_reason": rmeta.get("finish_reason"),
        "latency_ms": latency_ms,
        "usage": usage,  # verbatim usage_metadata
        "raw": raw,
    }


def _build_prompt_preview(prompt_template, prompt_inputs) -> str:
    """Best-effort render of the resolved prompt for the usage log."""
    try:
        if hasattr(prompt_template, "format"):
            return prompt_template.format(**(prompt_inputs or {}))
        return str(prompt_template)
    except Exception:
        return str(prompt_inputs)[:200]


def _record_call_usage(*, provider, model, token_info, duration_ms, prompt_template, prompt_inputs):
    """Persist the usage row via record_ai_usage(). No cost — the orchestrator
    computes USD cost from the verbatim `raw` blob at read time."""
    try:
        from teenyfactories.usage_recorder import log_usage

        log_usage(
            call_kind="llm",
            provider=provider,
            model=model,
            raw=token_info.get("raw"),
            latency_ms=duration_ms,
            request_id=str(uuid.uuid4()),
            chat_id=None,
        )
    except Exception as usage_err:
        # log_usage already swallows internally; this only fires if the
        # import itself blows up.
        log_warn(f"💬 LLM usage_recorder unavailable: {usage_err}")


# =============================================================================
# Public: call_llm — LEGACY
# =============================================================================


# LEGACY: superseded by the fluent `tf.llm().ask()` / `.ask_with_meta()` builder
# (teenyfactories/llm/builder.py). Left byte-for-byte — this IS the
# PydanticOutputParser structured-output path, which the builder reuses as its
# fallback. Remove once all call sites migrate to tf.llm() (call_llm-sweep epic).
def call_llm(
    prompt_template,
    prompt_inputs,
    response_model: Optional[Type[T]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
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
        max_tokens:      Optional cap on the number of OUTPUT tokens the model
                         may generate for this call. Default None = leave it to
                         the provider/langchain default (e.g. ChatAnthropic caps
                         output at 1024) — when None, nothing is passed to the
                         client and behaviour is byte-for-byte unchanged. Set a
                         larger value (e.g. 8192) for long structured responses
                         that would otherwise truncate. Mapped to each provider's
                         native kwarg (langchain `max_tokens`, Google
                         `max_output_tokens`, Ollama `num_predict`, Azure o3
                         `max_completion_tokens`). Passed through to the client
                         constructor — never mutates a shared client instance.

    Returns:
        Instance of `response_model` if one was given; otherwise the raw
        response text (str).

    Raises:
        Exception: any failure in client construction, invocation, or parsing.
                   Usage is still recorded in the finally block.
    """
    # LEGACY: deprecation breadcrumb (debug for now; will become a warn before removal).
    log_debug("💬 call_llm() is LEGACY and will be deprecated — migrate to tf.llm().ask() / .ask_with_meta()")
    start_time = time.time()
    success = False
    error_message = None
    token_info: dict = {}
    result_value = None
    used_provider = _resolve_provider(provider)
    used_model = _get_model_name(provider, model=model)

    try:
        # Spend-limit clearance — the orchestrator gates the call over :8998
        # BEFORE we issue the provider request. Pauses (SIGTERM-aware) while a
        # limit is breached; fails OPEN on any endpoint error so an orchestrator
        # hiccup never blocks real work. See teenyfactories/cost_clearance.py.
        try:
            from teenyfactories.cost_clearance import check_and_pause as _clearance_gate

            _clearance_gate()
        except Exception as clearance_err:
            log_warn(f"💬 LLM clearance check unavailable (proceeding): {clearance_err}")

        llm = get_llm_client(provider, model=model, temperature=temperature, max_tokens=max_tokens)

        if response_model is not None:
            prompt_template, parser = _prepare_prompt(prompt_template, prompt_inputs, response_model)
        else:
            parser = None

        raw_result, response_text = _invoke_chain(prompt_template, llm, prompt_inputs)

        if response_model is not None:
            response_text = clean_json_response(response_text)
            result_value = _parse_response(response_text, parser, response_model)
            log_debug(f"💬 LLM response parsed into {response_model.__name__}")
        else:
            result_value = response_text

        token_info = _extract_token_info(raw_result)
        success = True

    except Exception as e:
        error_message = str(e)
        log_error(f"💬 LLM call failed: {error_message}")

    finally:
        duration_ms = int((time.time() - start_time) * 1000)
        log_debug(f"💬 LLM usage: {used_provider}/{used_model} {duration_ms}ms")
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
