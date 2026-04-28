"""
Embedding API

Provider-agnostic text embedding via tf.embed().
Supports OpenAI and Ollama. Configured via environment variables:
  DEFAULT_EMBEDDING_PROVIDER — 'openai' (default) or 'ollama'
  DEFAULT_EMBEDDING_MODEL — model name (default: 'text-embedding-3-small')

Usage:
    import teenyfactories as tf

    vector = tf.embed("some text")
    vectors = tf.embed(["text 1", "text 2", "text 3"])
"""

import time
import uuid
from typing import List, Union

from . import config


def _log_embed_usage(provider: str, model: str, input_tokens: int,
                     latency_ms: int, preview_text: str) -> None:
    """Internal: record an embedding usage row. Never raises."""
    try:
        from .usage_recorder import log_usage
        log_usage(
            call_kind='embedding',
            provider=provider,
            model=model,
            input_tokens=int(input_tokens or 0),
            output_tokens=0,
            latency_ms=latency_ms,
            request_id=str(uuid.uuid4()),
            chat_id=None,
            prompt_preview=preview_text,
        )
    except Exception:
        pass


def embed(text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
    """Generate embedding vector(s) for text.

    Args:
        text: A single string or list of strings to embed.

    Returns:
        A single vector (list of floats) if input is a string,
        or a list of vectors if input is a list of strings.
    """
    provider = config.require_embedding_provider()
    model = config.require_embedding_model()

    single = isinstance(text, str)
    texts = [text] if single else text

    if not texts:
        return [] if not single else []

    if provider == 'openai':
        vectors = _embed_openai(texts, model)
    elif provider == 'ollama':
        vectors = _embed_ollama(texts, model)
    else:
        raise ValueError(f"Unknown embedding provider: {provider}")

    return vectors[0] if single else vectors


def _embed_openai(texts: List[str], model: str) -> List[List[float]]:
    """Embed via OpenAI API."""
    import openai
    api_key = config.require_api_key('openai')
    client = openai.OpenAI(api_key=api_key)

    preview = texts[0] if texts else ''

    # Batch in groups of 100 (OpenAI limit)
    all_vectors = []
    for i in range(0, len(texts), 100):
        batch = texts[i:i + 100]
        start = time.time()
        response = client.embeddings.create(model=model, input=batch)
        latency_ms = int((time.time() - start) * 1000)
        all_vectors.extend([r.embedding for r in response.data])

        # OpenAI embeddings response.usage exposes prompt_tokens / total_tokens.
        usage = getattr(response, 'usage', None)
        input_tokens = getattr(usage, 'prompt_tokens', None) if usage else None
        if input_tokens is None and usage is not None:
            input_tokens = getattr(usage, 'total_tokens', 0) or 0
        _log_embed_usage('openai', model, input_tokens or 0, latency_ms, preview)

    return all_vectors


def _embed_ollama(texts: List[str], model: str) -> List[List[float]]:
    """Embed via Ollama API."""
    import requests
    base_url = config.get('OLLAMA_BASE_URL', config.OLLAMA_DEFAULT_BASE_URL)

    preview = texts[0] if texts else ''
    all_vectors = []
    for text in texts:
        start = time.time()
        response = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        response.raise_for_status()
        latency_ms = int((time.time() - start) * 1000)
        all_vectors.append(response.json()['embedding'])

        # Ollama doesn't return token counts for embeddings — log 0.
        _log_embed_usage('ollama', model, 0, latency_ms, preview)

    return all_vectors
