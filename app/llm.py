from __future__ import annotations

from openai import OpenAI

from app.config import settings

SYSTEM_PROMPT = """You are a construction project assistant. Answer using only the provided document context.
When comparing documents, call out differences and alignments explicitly.
If context is insufficient, say what is missing. Cite source filenames. Be precise and practical."""


def generate_answer(question: str, context: str) -> str:
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=settings.openai_max_tokens,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Context from uploaded project files:\n\n{context}\n\n---\n\nQuestion: {question}",
            },
        ],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()
