"""
Embedding API

Provider-agnostic text embedding via tf.embed().
Supports OpenAI, Ollama, and OpenRouter. Defaults come from the environment:
  DEFAULT_EMBEDDING_PROVIDER — 'openai', 'ollama', or 'openrouter'
  DEFAULT_EMBEDDING_MODEL    — model name (e.g. 'text-embedding-3-small')

OpenRouter uses the existing OPENROUTER_API_KEY (no new env var) and exposes an
OpenAI-compatible embeddings endpoint, so a single OpenRouter key can serve both
LLM and embedding calls. With `usage:{include:true}` OpenRouter returns the
ACTUAL routed cost (USD) per call, which is stored verbatim for read-time
pricing by the orchestrator.

Dimension constraint (factory_vectors): embeddings are stored in fixed-dim
columns — 256/512/768/1024/1536/3072. Supported OpenRouter embedding models
that fit these columns:
  - baai/bge-m3                   → 1024-dim  (recommended default; cheap)
  - openai/text-embedding-3-small → 1536-dim
Models with other dimensions (e.g. qwen/qwen3-embedding-8b → 4096-dim) do NOT
fit any existing column — tf.store of such a vector will fail. Adding a 4096
column is a database-architect change; pick a fitting model until it exists.

Per-call overrides for both `provider` and `model` are accepted; useful for
factories that want to mix model sizes (e.g. small for chunks, large for
queries) without changing the global default.

Usage (input-first, lazy — the result IS the vector, computed on first access):
    import teenyfactories as tf

    vector  = tf.embed("some text")
    vectors = tf.embed(["text 1", "text 2", "text 3"])
    vector  = tf.embed("query").model("text-embedding-3-large")
    vector  = tf.embed("local").provider("ollama").model("nomic-embed-text")
    vector  = tf.embed("via OpenRouter").provider("openrouter").model("baai/bge-m3")
    vector, meta = tf.embed("query").with_meta()

LEGACY: the eager kwargs form `tf.embed(text, provider=, model=)` still works
(it pre-configures the builder) and logs a debug deprecation breadcrumb; migrate
to `tf.embed(text).provider(p).model(m)`.
"""

import time
import uuid
from typing import List, Optional, Union

from . import config
from .logging import log_debug


def _log_embed_usage(
    provider: str,
    model: str,
    input_tokens: int,
    latency_ms: int,
    preview_text: str,
    actual_cost: Optional[float] = None,
) -> None:
    """Internal: record an embedding usage row (verbatim — no cost computed
    here). The orchestrator computes USD cost from `raw` at read time.

    When `actual_cost` is supplied (e.g. OpenRouter returns `usage.cost` in
    USD), it is stored VERBATIM at `raw.response_metadata.token_usage.cost`,
    which the orchestrator's read-time coster (services/usageCostSql.js
    `actualCostExpr`, core/agent path) prefers over its own rate table. This
    is metadata capture, not tf-side pricing — tf computes no cost. For
    openai/ollama no actual cost is available, so `raw` stays token-only and
    the orchestrator prices openai via its pricing table (ollama is free).

    Never raises."""
    try:
        from .usage_recorder import log_usage

        raw = {
            "usage_metadata": {"input_tokens": int(input_tokens or 0)},
            "prompt_preview": (preview_text or "")[:80],
        }
        if actual_cost is not None:
            raw["response_metadata"] = {"token_usage": {"cost": actual_cost}}

        log_usage(
            call_kind="embedding",
            provider=provider,
            model=model,
            raw=raw,
            latency_ms=latency_ms,
            request_id=str(uuid.uuid4()),
            chat_id=None,
        )
    except Exception:
        pass


def embed(
    text: Union[str, List[str]],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> "_Embedding":
    """Embed text — input-first, lazy. The result *is* the vector (a ``list``
    subclass) for a single string, or a list-of-vectors for a list input; it
    computes on first value-access (memoised).

        vec        = tf.embed("hello")                    # -> vector (list)
        vec        = tf.embed("hi").model("baai/bge-m3")  # configured, still a vector
        vec, meta  = tf.embed("hi").with_meta()           # -> (vector, meta-dict)
        vecs       = tf.embed(["a", "b"])                 # -> list-of-vectors

    LEGACY: the old eager ``tf.embed(text, provider=, model=)`` kwargs still work
    (they pre-configure the builder); migrate to ``tf.embed(text).provider(p).model(m)``.
    """
    if provider is not None or model is not None:
        # LEGACY: kwargs form — debug for now; will become a warn before removal.
        log_debug(
            "🔢 tf.embed(text, provider=, model=) kwargs are LEGACY — "
            "migrate to tf.embed(text).provider(p).model(m)"
        )
    return _Embedding(text, provider, model)


def _embed_compute(text, provider, model):
    """Eager embed (the original dispatch). Returns ``(result, meta)`` where
    result is a vector (str input) or list-of-vectors (list input), and meta is
    the read-view dict {provider, model, cost, finish_reason, latency_ms, usage, raw}."""
    used_provider = provider or config.require_embedding_provider()
    used_model = model or config.require_embedding_model()

    single = isinstance(text, str)
    texts = [text] if single else list(text or [])

    if not texts:
        usage = {"provider": used_provider, "model": used_model, "input_tokens": 0, "cost": None, "latency_ms": 0}
    elif used_provider == "openai":
        vectors, usage = _embed_openai(texts, used_model)
    elif used_provider == "ollama":
        vectors, usage = _embed_ollama(texts, used_model)
    elif used_provider == "openrouter":
        vectors, usage = _embed_openrouter(texts, used_model)
    else:
        raise ValueError(f"Unknown embedding provider: {used_provider}")

    result = [] if not texts else (vectors[0] if single else vectors)
    meta = {
        "provider": used_provider,
        "model": used_model,
        "cost": usage.get("cost"),
        "finish_reason": None,
        "latency_ms": usage.get("latency_ms"),
        "usage": {"input_tokens": usage.get("input_tokens")},
        "raw": {},
    }
    return result, meta


class _Embedding(list):
    """Lazy, input-first embedding result. Behaves as the vector (or list-of-
    vectors for batch input); computes on first value-access. Configure with
    ``.model()`` / ``.provider()`` before use; ``.with_meta()`` resolves to
    ``(vector, meta)``. A pure value — no side effects — so laziness is safe
    (a list subclass consumed via the list protocol)."""

    def __init__(self, text, provider=None, model=None):
        super().__init__()
        self._text = text
        self._provider = provider
        self._model = model
        self._resolved = False
        self._meta = None

    def model(self, name):
        self._model = name
        return self

    def provider(self, name):
        self._provider = name
        return self

    def with_meta(self):
        self._resolve()
        return (self, self._meta)

    def _resolve(self):
        if self._resolved:
            return
        self._resolved = True  # set first so populate can't recurse via list protocol
        result, meta = _embed_compute(self._text, self._provider, self._model)
        list.extend(self, result)
        self._meta = meta

    # resolution triggers — anything that reads the value
    def __len__(self):
        self._resolve()
        return super().__len__()

    def __iter__(self):
        self._resolve()
        return super().__iter__()

    def __getitem__(self, i):
        self._resolve()
        return super().__getitem__(i)

    def __contains__(self, x):
        self._resolve()
        return super().__contains__(x)

    def __eq__(self, other):
        self._resolve()
        return list(self) == other

    def __repr__(self):
        self._resolve()
        return super().__repr__()

    def __bool__(self):
        self._resolve()
        return super().__len__() > 0

    __hash__ = None


def _embed_openai(texts: List[str], model: str) -> List[List[float]]:
    """Embed via OpenAI API."""
    import openai

    api_key = config.require_api_key("openai")
    client = openai.OpenAI(api_key=api_key)

    preview = texts[0] if texts else ""

    # Batch in groups of 100 (OpenAI limit)
    all_vectors = []
    total_in, total_lat = 0, 0
    for i in range(0, len(texts), 100):
        batch = texts[i : i + 100]
        start = time.time()
        response = client.embeddings.create(model=model, input=batch)
        latency_ms = int((time.time() - start) * 1000)
        all_vectors.extend([r.embedding for r in response.data])

        # OpenAI embeddings response.usage exposes prompt_tokens / total_tokens.
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        if input_tokens is None and usage is not None:
            input_tokens = getattr(usage, "total_tokens", 0) or 0
        _log_embed_usage("openai", model, input_tokens or 0, latency_ms, preview)
        total_in += input_tokens or 0
        total_lat += latency_ms

    return all_vectors, {
        "provider": "openai",
        "model": model,
        "input_tokens": total_in,
        "cost": None,
        "latency_ms": total_lat,
    }


def _embed_openrouter(texts: List[str], model: str) -> List[List[float]]:
    """Embed via OpenRouter's OpenAI-compatible embeddings endpoint.

    OpenRouter exposes POST /api/v1/embeddings with the same request/response
    shape as OpenAI, so we reuse the OpenAI SDK with base_url pointed at
    OpenRouter (same pattern as the OpenRouter LLM provider). Passing
    `usage: {include: true}` makes OpenRouter return its ACTUAL routed cost in
    `response.usage.cost` (USD); that cost is stored verbatim into `raw` so
    the orchestrator prices the row off the real spend rather than a static
    table (OpenRouter routes to whichever upstream is cheapest/available).

    Dimension constraint: factory_vectors stores embeddings in fixed-dim
    columns (256/512/768/1024/1536/3072). Supported OpenRouter models that
    FIT today:
      - baai/bge-m3                  → 1024-dim  (recommended default; cheap)
      - openai/text-embedding-3-small → 1536-dim
    Models whose output dimension is NOT one of those columns (e.g.
    qwen/qwen3-embedding-8b → 4096-dim) will FAIL to store: tf.store of a
    4096-vector has no matching column. Such models need a new factory_vectors
    column added first (database-architect territory) — do NOT pick them
    until that exists.
    """
    import openai

    api_key = config.require_api_key("openrouter")
    base_url = config.get("OPENROUTER_INFERENCE_URL", "https://openrouter.ai/api/v1")
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    preview = texts[0] if texts else ""

    # Batch in groups of 100 (mirrors the OpenAI batch limit).
    all_vectors = []
    total_in, total_lat, total_cost, saw_cost = 0, 0, 0.0, False
    for i in range(0, len(texts), 100):
        batch = texts[i : i + 100]
        start = time.time()
        # extra_body forwards `usage: {include: true}` into the POST body so
        # OpenRouter populates response.usage.cost with the real USD spend.
        response = client.embeddings.create(
            model=model,
            input=batch,
            extra_body={"usage": {"include": True}},
        )
        latency_ms = int((time.time() - start) * 1000)
        all_vectors.extend([r.embedding for r in response.data])

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        if input_tokens is None and usage is not None:
            input_tokens = getattr(usage, "total_tokens", 0) or 0
        # OpenRouter returns the actual routed cost (USD) on usage.cost when
        # usage.include is set; store it verbatim for read-time pricing.
        actual_cost = getattr(usage, "cost", None) if usage else None
        _log_embed_usage("openrouter", model, input_tokens or 0, latency_ms, preview, actual_cost=actual_cost)
        total_in += input_tokens or 0
        total_lat += latency_ms
        if actual_cost is not None:
            total_cost += actual_cost
            saw_cost = True

    return all_vectors, {
        "provider": "openrouter",
        "model": model,
        "input_tokens": total_in,
        "cost": (total_cost if saw_cost else None),
        "latency_ms": total_lat,
    }


def _embed_ollama(texts: List[str], model: str) -> List[List[float]]:
    """Embed via Ollama API."""
    import requests

    base_url = config.get("OLLAMA_BASE_URL", config.OLLAMA_DEFAULT_BASE_URL)

    preview = texts[0] if texts else ""
    all_vectors = []
    total_lat = 0
    for text in texts:
        start = time.time()
        response = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        response.raise_for_status()
        latency_ms = int((time.time() - start) * 1000)
        all_vectors.append(response.json()["embedding"])

        # Ollama doesn't return token counts for embeddings — log 0.
        _log_embed_usage("ollama", model, 0, latency_ms, preview)
        total_lat += latency_ms

    return all_vectors, {"provider": "ollama", "model": model, "input_tokens": 0, "cost": None, "latency_ms": total_lat}
