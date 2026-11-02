"""Draft ProjNet comment, evaluation, and backcheck text on a local model.

Comments are CUI in real reviews. The model URL has to be this machine.
"""

from __future__ import annotations

from urllib.parse import urlparse

from openai import OpenAI

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

_KIND_INSTRUCTION = {
    "comment": (
        "Draft the reviewer comment a person will paste into ProjNet. "
        "One issue, the document and section given, and what should change."
    ),
    "evaluation": (
        "Draft the evaluator response a person will paste into ProjNet. "
        "Say whether the designer concurs, and what will change in the documents. "
        "If the comment is right, concur. If the materials do not support it, say what is missing."
    ),
    "backcheck": (
        "Draft the backcheck a person will paste into ProjNet. "
        "Accept the response only if it actually closes the comment. "
        "Otherwise say what is still open."
    ),
}


class LocalModelRequired(Exception):
    pass


def assert_local_model(base_url: str) -> str:
    url = (base_url or "").strip()
    if not url:
        raise LocalModelRequired(
            "Set DRCHECKS_BASE_URL to http://127.0.0.1:11434/v1. This app does not call OpenAI."
        )
    host = (urlparse(url).hostname or "").lower()
    if host not in LOCAL_HOSTS or "openai.com" in url.lower():
        raise LocalModelRequired(
            "Review comments stay on this Mac. Point DRCHECKS_BASE_URL at Ollama on 127.0.0.1."
        )
    return url


def draft_prompt(record: dict, kind: str, note: str, publication_facts: list[dict[str, str]]) -> str:
    instruction = _KIND_INSTRUCTION[kind]
    facts = "\n".join(f"- {item['detail']}" for item in publication_facts) or "- None cited."
    return f"""{instruction}

Use only the fields and note below, plus the publication facts. Do not invent dimensions, sheet notes, or code sections. Do not declare a UFC or UFGS inactive unless the note already says so. If a fact says to confirm a publication, tell the reviewer to confirm it; do not decide currency yourself.

Discipline: {record.get("discipline") or "—"}
Document: {record.get("document") or "—"}
Section: {record.get("section") or "—"}
Status: {record.get("status") or "—"}
Comment:
{record.get("comment") or "—"}

Evaluation already in the export:
{record.get("response") or "—"}

Backcheck already in the export:
{record.get("backcheck") or "—"}

Note from the person drafting this:
{note or "—"}

Publication facts:
{facts}

Write only the text to paste. No title and no preamble.
"""


def draft_text(
    record: dict,
    kind: str,
    note: str,
    publication_facts: list[dict[str, str]],
    *,
    base_url: str,
    api_key: str,
    model: str,
) -> str:
    if kind not in _KIND_INSTRUCTION:
        raise ValueError("Draft must be a comment, an evaluation, or a backcheck.")
    url = assert_local_model(base_url)
    client = OpenAI(api_key=api_key or "ollama", base_url=url)
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": "You draft design-review text for a person to paste into ProjNet DrChecks.",
            },
            {
                "role": "user",
                "content": draft_prompt(record, kind, note, publication_facts),
            },
        ],
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise RuntimeError("The local model returned an empty draft.")
    return text
