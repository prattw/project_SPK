from __future__ import annotations

import anthropic

from app.config import settings

SYSTEM_PROMPT = """You are a construction project assistant. Answer using only the provided document context.
When comparing documents, call out differences and alignments explicitly.
If context is insufficient, say what is missing. Cite source filenames. Be precise and practical."""


def generate_answer(question: str, context: str) -> str:
    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Context from uploaded project files:\n\n{context}\n\n---\n\nQuestion: {question}",
            }
        ],
        temperature=0.2,
    )
    parts = [block.text for block in message.content if block.type == "text"]
    return "\n".join(parts).strip()
