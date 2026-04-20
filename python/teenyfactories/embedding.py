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

import os
from typing import List, Union

from .logging import log_info, log_error


def embed(text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
    """Generate embedding vector(s) for text.

    Args:
        text: A single string or list of strings to embed.

    Returns:
        A single vector (list of floats) if input is a string,
        or a list of vectors if input is a list of strings.
    """
    provider = os.getenv('DEFAULT_EMBEDDING_PROVIDER', 'openai')
    model = os.getenv('DEFAULT_EMBEDDING_MODEL', 'text-embedding-3-small')

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
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set — required for OpenAI embeddings")

    client = openai.OpenAI(api_key=api_key)

    # Batch in groups of 100 (OpenAI limit)
    all_vectors = []
    for i in range(0, len(texts), 100):
        batch = texts[i:i + 100]
        response = client.embeddings.create(model=model, input=batch)
        all_vectors.extend([r.embedding for r in response.data])

    return all_vectors


def _embed_ollama(texts: List[str], model: str) -> List[List[float]]:
    """Embed via Ollama API."""
    import requests
    base_url = os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')

    all_vectors = []
    for text in texts:
        response = requests.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
        )
        response.raise_for_status()
        all_vectors.append(response.json()['embedding'])

    return all_vectors
