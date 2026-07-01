"""Extra retrieval passes for USACE / UFC / statutory library documents."""

from __future__ import annotations

import re

from app.usace_dates import load_official_publications, normalize_doc_number

REGULATORY_KEYWORDS = re.compile(
    r"\b("
    r"USACE|UFC|UFGS|Unified Facilities|Engineer Regulation|Engineer Manual|"
    r"US Code|U\.S\. Code|FAR|DFARS|AFARS|submittal procedure|regulation|"
    r"standard|criteria|compliance|conform|conformance"
    r")\b",
    re.IGNORECASE,
)

_PUBLICATION_REF_RE = re.compile(
    r"\b(ER|EM|EP|EC)\s+([\d]+(?:[-\s][\d]+)+)\b",
    re.IGNORECASE,
)

LIBRARY_SUBQUERIES: tuple[str, ...] = (
    "UFC UFGS unified facilities guide specifications submittal product data requirements",
    "USACE Engineer Regulation Engineer Manual submittal review procedures",
    "Section 01 33 00 submittal procedures product data SD-03",
    "resilient flooring tile cork flooring specifications submittal",
    "US Code federal acquisition construction contract requirements",
)


def question_needs_library_regulations(question: str) -> bool:
    return bool(REGULATORY_KEYWORDS.search(question))


def _normalize_doc_number(series: str, number_part: str) -> str:
    """Turn '1110-1-8156' / '1110 1 8156' into 'ER 1110-1-8156'."""
    parts = [p for p in re.split(r"[-\s]+", number_part.strip()) if p]
    if not parts:
        return f"{series.upper()} {number_part}".strip()
    return f"{series.upper()} {'-'.join(parts)}"


def expand_publication_refs(
    question: str,
    library_docs: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Map publication citations in a question to indexed doc_number values.

    Handles full numbers (e.g. 'ER 1110-1-8156') and short forms such as
    'ER 8156' by matching against the Document Library catalog.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(doc_number: str) -> None:
        key = doc_number.strip().upper()
        if key and key not in seen:
            seen.add(key)
            found.append(doc_number.strip())

    for match in _PUBLICATION_REF_RE.finditer(question):
        add(_normalize_doc_number(match.group(1), match.group(2)))

    q_upper = question.upper()
    wants_8156 = bool(
        re.search(r"\bER\s*8156\b", q_upper)
        or (re.search(r"\b8156\b", q_upper) and re.search(r"\bER\b", q_upper))
    )
    if wants_8156:
        for doc in library_docs or []:
            doc_number = doc.get("doc_number") or ""
            if "8156" in doc_number.upper() and doc_number.upper().startswith("ER"):
                add(doc_number)
        # Fall back to the official publication catalog when the vector index
        # has not yet ingested these ERs (common on local dev).
        if not found:
            catalog = load_official_publications()
            for key, row in catalog.items():
                if "8156" in key and key.startswith("ER"):
                    add(row.get("pub_number") or key)

    return found


def library_subqueries(question: str) -> list[str]:
    """Build semantic search queries aimed at the indexed regulatory corpus."""
    base = question.strip()
    queries = [base]
    if question_needs_library_regulations(base):
        queries.extend(LIBRARY_SUBQUERIES)
    if re.search(r"\b8156\b", base, re.IGNORECASE):
        queries.extend(
            [
                "ER 1110-1-8156 geospatial data systems policies guidance requirements",
                "ER 1110-2-8156 water control manuals engineering design errata",
                "ER 8156 instrumentation data collection communication networks standards",
            ]
        )
    # De-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out
