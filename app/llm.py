from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from openai import BadRequestError, OpenAI

from app.config import settings

RESPONSE_FORMAT = """Format every answer like a USACE technical review:

- Organize with clear markdown headings and numbered items (e.g., "COMPLIANT ITEMS:", "NON-COMPLIANT OR MISSING ITEMS:", "Legal Requirements:", "Recommended Approach:" — choose headings that fit the question).
- For compliance or comparison questions, label each item in bold with a status: **COMPLIANT**, **NON-COMPLIANT**, **PARTIALLY COMPLIANT**, or **NOT VERIFIED**, followed by the evidence.
- Quote the source documents directly when the wording matters, and cite each fact inline using markdown links:
  `[Document Number, Page N](url)` — e.g. `[ER 415-1-10, Page 42](https://www.publications.usace.army.mil/...)`.
  Use the exact URL provided in the Available citations list when present.
- End with a "SUMMARY:" or "Recommended Approach:" section when the answer supports a decision.
- Then add a "Confidence Assessment:" — a level (High/Medium/Low) with a percentage and 1-3 sentences explaining what would raise or lower it (missing documents, possible newer revisions, items likely addressed elsewhere).
- Close with this disclaimer:
  "AI Disclaimer: This analysis is an informational aid only and does not replace the judgment, review, or responsibility of qualified USACE personnel. Verify all findings against current regulations and contract requirements."
"""

SYSTEM_PROMPT = f"""You are a construction and federal acquisition assistant for USACE (U.S. Army Corps of Engineers) projects.
Answer using only the provided document context.

The document library follows USACE and federal acquisition conventions:
- ER (Engineer Regulations), EM (Engineer Manuals), EP (Engineer Pamphlets), EC (Engineer Circulars), ECB (Engineering & Construction Bulletins)
- CECW/CECI/CEMP memos (HQ policy), FAR, DFARS, AFARS, PGI (acquisition regulations)
- UAI/UDG (USACE Acquisition Instruction and Desk Guide), including IDaC (Integrated Design and Construction)

When citing, include the document number when shown in the context label (e.g., "ER 1110-345-721").
Authoritative public sources for current versions: USACE Publications (publications.usace.army.mil),
the USACE Library Program, ERDC Library, and USACE Geospatial Open Data.

When comparing documents, call out differences and alignments explicitly.
If context is insufficient, say what is missing. Be precise and practical.

{RESPONSE_FORMAT}"""


GENERAL_SYSTEM_PROMPT = f"""You are a construction and federal acquisition assistant for USACE (U.S. Army Corps of Engineers) projects.
No project documents have been uploaded, so answer from your general knowledge of USACE policies,
publications, and federal acquisition practice.

Be familiar with USACE and federal acquisition conventions:
- ER (Engineer Regulations), EM (Engineer Manuals), EP (Engineer Pamphlets), EC (Engineer Circulars), ECB (Engineering & Construction Bulletins)
- ETL (Engineer Technical Letters), UFC (Unified Facilities Criteria), UFGS (Unified Facilities Guide Specifications)
- CECW/CECI/CEMP memos (HQ policy), FAR, DFARS, AFARS, PGI (acquisition regulations)
- UAI/UDG (USACE Acquisition Instruction and Desk Guide), including IDaC (Integrated Design and Construction)
- AR (Army Regulations) and DA PAM (Army Pamphlets)

Cite document numbers when you reference specific publications (e.g., "ER 1110-345-721"), and recommend
verifying current versions at USACE Publications (publications.usace.army.mil) or the Whole Building
Design Guide (wbdg.org) for UFC/UFGS. Be precise and practical. If you are not certain, say so.

{RESPONSE_FORMAT}

Since no documents are uploaded, cite publication numbers from general knowledge instead of page-level
citations, and reflect the lack of project documents in the Confidence Assessment."""


def _chat(messages: list[dict[str, str]]) -> str:
    """Call the chat API, handling parameter differences between model generations.

    Newer OpenAI models (gpt-5 family, o-series) require max_completion_tokens
    and reject custom temperature; older ones (gpt-4o family) accept max_tokens.
    """
    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=settings.openai_api_key)
    kwargs: dict = {
        "model": settings.openai_model,
        "messages": messages,
        "max_completion_tokens": settings.openai_max_tokens,
        "temperature": 0.2,
    }
    for _ in range(3):
        try:
            response = client.chat.completions.create(**kwargs)
            return (response.choices[0].message.content or "").strip()
        except BadRequestError as exc:
            param = getattr(exc, "param", None) or ""
            message = str(exc)
            if "max_completion_tokens" in message and "max_completion_tokens" in kwargs:
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                continue
            if param == "temperature" or "'temperature'" in message:
                kwargs.pop("temperature", None)
                continue
            raise
    raise RuntimeError("Could not find compatible parameters for the configured model")


IMAGE_ANALYSIS_PROMPT = """You are analyzing an image uploaded to a USACE construction-document assistant so it can be searched and reviewed later.

Produce two clearly labeled sections:

1. "TRANSCRIBED TEXT:" — Transcribe ALL visible text verbatim, exactly as written (labels, titles, callouts, dimensions, table cells, stamps, signatures, logos with wordmarks, sheet numbers, revision blocks). Preserve numbers, units, and codes precisely. If there is no readable text, write "(none)".

2. "VISUAL DESCRIPTION:" — Describe what the image shows in detail useful for a construction/engineering review: the type of image (photo, drawing, diagram, chart, logo, screenshot, scanned document), key objects or elements, colors, layout, any branding/logos and their colors/wording, and anything that looks like a defect, condition, or notable detail. If it is a chart or table, summarize the data it conveys.

Be thorough and factual. Do not invent text or details that are not present."""

PDF_OCR_PROMPT = """This is a scanned or image-based page from a USACE construction/engineering document. Transcribe ALL text on the page verbatim, exactly as written.

- Preserve headings, section numbers, lists, and paragraph order.
- Render tables row by row using " | " between cells.
- Keep numbers, units, codes, and references precise.
- Output only the transcribed text — no commentary, no description.
- If the page has no readable text, output nothing."""

IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _vision_call(data: bytes, mime: str, prompt: str) -> str:
    """Send image bytes + a prompt to the configured vision model.

    Returns the model text, or "" if not possible (no key, empty data, or
    model/network error). Callers decide on a fallback.
    """
    if not settings.openai_api_key or not data:
        return ""
    b64 = base64.b64encode(data).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    try:
        return _chat(messages)
    except Exception:
        return ""


def describe_image(path: str | Path) -> str:
    """OCR + visual description of an image via the configured vision model."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return ""
    mime = IMAGE_MIME_BY_EXT.get(p.suffix.lower()) or mimetypes.guess_type(str(p))[0] or "image/png"
    return _vision_call(data, mime, IMAGE_ANALYSIS_PROMPT)


def ocr_image_bytes(data: bytes, mime: str = "image/png") -> str:
    """Verbatim OCR of a rendered page image (used for scanned PDF pages)."""
    return _vision_call(data, mime, PDF_OCR_PROMPT)


def _format_citation_block(citations: list[dict] | None) -> str:
    if not citations:
        return ""
    lines = ["Available citations (use these exact URLs in markdown links):"]
    for c in citations[:24]:
        label = c.get("label") or c.get("source") or "Source"
        url = c.get("url") or ""
        lines.append(f"- [{label}]({url})")
    return "\n".join(lines) + "\n\n"


def _history_messages(history: list[dict[str, str]] | None, limit: int = 8) -> list[dict[str, str]]:
    if not history:
        return []
    out: list[dict[str, str]] = []
    for item in history[-limit:]:
        role = item.get("role")
        content = (item.get("content") or item.get("text") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


def generate_general_answer(
    question: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    messages = [{"role": "system", "content": GENERAL_SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": question})
    return _chat(messages)


def generate_answer(
    question: str,
    context: str,
    history: list[dict[str, str]] | None = None,
    citations: list[dict] | None = None,
) -> str:
    citation_block = _format_citation_block(citations)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_history_messages(history))
    messages.append(
        {
            "role": "user",
            "content": (
                f"{citation_block}"
                f"Context from uploaded project files:\n\n{context}\n\n---\n\nQuestion: {question}"
            ),
        }
    )
    return _chat(messages)
