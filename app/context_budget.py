from __future__ import annotations

from typing import Any

from app.config import settings

TRUNCATION_NOTICE = (
    "\n\n[… document truncated for indexing — ask about specific sections or upload excerpts …]"
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token for English prose)."""
    return max(1, len(text) // 4)


def prepare_text_for_ingest(text: str, source: str) -> tuple[str, list[str]]:
    """Cap extracted text before chunking so ingest stays bounded."""
    warnings: list[str] = []
    limit = settings.max_extract_chars_per_file

    if len(text) <= limit:
        return text, warnings

    warnings.append(
        f"{source}: extracted text truncated from {len(text):,} to {limit:,} characters for indexing."
    )
    return text[:limit] + TRUNCATION_NOTICE, warnings


def cap_chunks(chunks: list[str], source: str) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    limit = settings.max_chunks_per_file
    if len(chunks) <= limit:
        return chunks, warnings

    warnings.append(
        f"{source}: only the first {limit:,} chunks were indexed "
        f"({len(chunks):,} chunks would have exceeded the per-file limit)."
    )
    return chunks[:limit], warnings


def cap_chunk_records(
    records: list[tuple[str, dict]],
    source: str,
) -> tuple[list[tuple[str, dict]], list[str]]:
    warnings: list[str] = []
    limit = settings.max_chunks_per_file
    if len(records) <= limit:
        return records, warnings

    warnings.append(
        f"{source}: only the first {limit:,} of {len(records):,} page chunks were indexed."
    )
    return records[:limit], warnings


def format_chunk_for_prompt(chunk: dict) -> str:
    source = chunk.get("source", "unknown")
    doc_number = chunk.get("doc_number")
    name = f"{doc_number} — {source}" if doc_number else source

    page_start = chunk.get("page_start")
    page_end = chunk.get("page_end")
    spec_section = chunk.get("spec_section")
    section_note = f", Section {spec_section}" if spec_section else ""
    if page_start is not None:
        if page_end and page_end != page_start:
            label = f"{name} (pages {page_start}–{page_end}{section_note})"
        else:
            label = f"{name} (page {page_start}{section_note})"
    elif spec_section:
        label = f"{name} (Section {spec_section})"
    else:
        label = name
    return f"[{label}]\n{chunk['text']}"


def pack_chunks_for_llm(
    chunks: list[dict[str, Any]],
    *,
    focus_sources: list[str] | None = None,
    min_library_chunks: int = 0,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """
    Select chunks for the LLM prompt without exceeding max_context_chars.
    Spreads selections across sources so multi-document comparison stays fair.
    When session uploads are focused, reserves space for Document Library chunks.
    """
    warnings: list[str] = []
    if not chunks:
        return "", [], warnings

    budget = settings.max_context_chars
    per_source_cap = settings.max_chunks_per_source
    focus_set = set(focus_sources or [])

    ranked = sorted(
        chunks,
        key=lambda c: (-(c.get("score") or 0), str(c.get("source", ""))),
    )

    preselected: list[dict[str, Any]] = []
    if min_library_chunks > 0 and focus_set:
        library_ranked = [c for c in ranked if c.get("source") not in focus_set]
        preselected = library_ranked[:min_library_chunks]

    selected: list[dict[str, Any]] = list(preselected)
    used_chars = 0
    per_source: dict[str, int] = {}

    for chunk in preselected:
        block = format_chunk_for_prompt(chunk)
        used_chars += len(block) + (10 if used_chars else 0)
        source = str(chunk.get("source", "unknown"))
        per_source[source] = per_source.get(source, 0) + 1

    pre_keys = {
        f"{c.get('source')}:{c.get('page_start')}:{c.get('text', '')[:80]}" for c in preselected
    }

    for chunk in ranked:
        key = f"{chunk.get('source')}:{chunk.get('page_start')}:{chunk.get('text', '')[:80]}"
        if key in pre_keys:
            continue

        source = str(chunk.get("source", "unknown"))
        if per_source.get(source, 0) >= per_source_cap:
            continue

        block = format_chunk_for_prompt(chunk)
        separator = 10 if selected else 0
        need = len(block) + separator

        if used_chars + need > budget:
            continue

        selected.append(chunk)
        per_source[source] = per_source.get(source, 0) + 1
        used_chars += need

    skipped = len(ranked) - len(selected)
    if skipped:
        warnings.append(
            f"Retrieved {len(ranked)} candidate chunks; sent {len(selected)} to the model "
            f"({used_chars:,} / {budget:,} character budget)."
        )

    if preselected:
        warnings.append(
            f"Reserved {len(preselected)} section(s) from the Document Library for USACE/UFC context."
        )

    if not selected and ranked:
        top = ranked[0]
        source = str(top.get("source", "unknown"))
        room = budget - len(f"[{source}]\n")
        if room > 200:
            trimmed = {**top, "text": top["text"][: room] + "\n[… truncated to fit context window …]"}
            selected = [trimmed]
            warnings.append("Context budget required trimming the top retrieved chunk.")

    context = "\n\n---\n\n".join(format_chunk_for_prompt(c) for c in selected)
    return context, selected, warnings
