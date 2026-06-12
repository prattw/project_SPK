from __future__ import annotations

from openai import OpenAI

from app.config import settings

SYSTEM_PROMPT = """You are a construction and federal acquisition assistant for USACE (U.S. Army Corps of Engineers) projects.
Answer using only the provided document context.

The document library follows USACE and federal acquisition conventions:
- ER (Engineer Regulations), EM (Engineer Manuals), EP (Engineer Pamphlets), EC (Engineer Circulars), ECB (Engineering & Construction Bulletins)
- CECW/CECI/CEMP memos (HQ policy), FAR, DFARS, AFARS, PGI (acquisition regulations)
- UAI/UDG (USACE Acquisition Instruction and Desk Guide), including IDaC (Integrated Design and Construction)

When citing, include the document number when shown in the context label (e.g., "ER 1110-345-721").
Authoritative public sources for current versions: USACE Publications (publications.usace.army.mil),
the USACE Library Program, ERDC Library, and USACE Geospatial Open Data.

When comparing documents, call out differences and alignments explicitly.
If context is insufficient, say what is missing. Be precise and practical."""


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
