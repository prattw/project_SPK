from __future__ import annotations

import time

from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from app.config import settings
from app.token_usage import record_embedding_tokens

# Transient OpenAI errors worth retrying with backoff (rate limits, network blips).
_RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
_MAX_RETRIES = 6


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    batch_size = settings.embedding_batch_size
    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        all_embeddings.extend(_embed_batch(batch))

    return all_embeddings


def _embed_batch(texts: list[str]) -> list[list[float]]:
    provider = settings.embedding_provider.lower()

    if provider == "voyage":
        return _embed_voyage(texts)
    if provider == "openai":
        return _embed_openai(texts)

    raise ValueError(f"Unknown embedding provider: {provider}")


def _embed_voyage(texts: list[str]) -> list[list[float]]:
    if not settings.voyage_api_key:
        raise ValueError("VOYAGE_API_KEY is not configured")

    import voyageai

    client = voyageai.Client(api_key=settings.voyage_api_key)
    result = client.embed(texts, model=settings.voyage_embedding_model, input_type="document")
    return result.embeddings


def _embed_openai(texts: list[str]) -> list[list[float]]:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured (needed for OpenAI embeddings)")

    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
    delay = 2.0
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.embeddings.create(
                model=settings.openai_embedding_model,
                input=texts,
            )
            usage = getattr(response, "usage", None)
            if usage:
                record_embedding_tokens(getattr(usage, "total_tokens", 0) or 0)
            return [item.embedding for item in response.data]
        except _RETRYABLE:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    raise RuntimeError("Unreachable: embedding retries exhausted")


def embed_query(text: str) -> list[float]:
    provider = settings.embedding_provider.lower()

    if provider == "voyage":
        if not settings.voyage_api_key:
            raise ValueError("VOYAGE_API_KEY is not configured")
        import voyageai

        client = voyageai.Client(api_key=settings.voyage_api_key)
        result = client.embed([text], model=settings.voyage_embedding_model, input_type="query")
        return result.embeddings[0]

    if provider == "openai":
        return embed_texts([text])[0]

    raise ValueError(f"Unknown embedding provider: {provider}")
