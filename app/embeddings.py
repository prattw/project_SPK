from __future__ import annotations

from openai import OpenAI

from app.config import settings


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

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.embeddings.create(
        model=settings.openai_embedding_model,
        input=texts,
    )
    return [item.embedding for item in response.data]


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
