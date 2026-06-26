"""Extra retrieval passes for USACE / UFC / statutory library documents."""

from __future__ import annotations

import re

REGULATORY_KEYWORDS = re.compile(
    r"\b("
    r"USACE|UFC|UFGS|Unified Facilities|Engineer Regulation|Engineer Manual|"
    r"US Code|U\.S\. Code|FAR|DFARS|AFARS|submittal procedure|regulation|"
    r"standard|criteria|compliance"
    r")\b",
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


def library_subqueries(question: str) -> list[str]:
    """Build semantic search queries aimed at the indexed regulatory corpus."""
    base = question.strip()
    queries = [base]
    if question_needs_library_regulations(base):
        queries.extend(LIBRARY_SUBQUERIES)
    # De-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out
