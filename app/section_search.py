"""MasterFormat / UFGS section number detection for spec PDF retrieval."""

from __future__ import annotations

import re

# CSI MasterFormat: 09 62 29, 09-62-29, Section 09 62 29, etc.
SECTION_NUMBER_RE = re.compile(
    r"\b(\d{2})\s*[-\u2010-\u2014]?\s*(\d{2})\s*[-\u2010-\u2014]?\s*(\d{2})\b"
)

SECTION_HEADER_RE = re.compile(
    r"(?:SECTION|Section|SEC\.?)\s+(\d{2}\s+\d{2}\s+\d{2})",
    re.IGNORECASE,
)

SECTION_TITLE_LINE_RE = re.compile(
    r"^(\d{2}\s+\d{2}\s+\d{2})\s+([A-Z][A-Z0-9 \-/]{2,80})$",
    re.MULTILINE,
)


def normalize_section_number(raw: str) -> str:
    """Return canonical 'DD DD DD' section number or empty string."""
    text = (raw or "").strip()
    if not text:
        return ""
    match = SECTION_NUMBER_RE.search(text.replace("-", " "))
    if not match:
        return ""
    return f"{match.group(1)} {match.group(2)} {match.group(3)}"


def extract_section_numbers(text: str) -> list[str]:
    """Find all distinct MasterFormat section numbers mentioned in text."""
    found: list[str] = []
    seen: set[str] = set()
    for match in SECTION_NUMBER_RE.finditer(text):
        norm = f"{match.group(1)} {match.group(2)} {match.group(3)}"
        if norm not in seen:
            seen.add(norm)
            found.append(norm)
    return found


def section_search_variants(section: str) -> list[str]:
    """Text needles for $contains lookups (most specific first)."""
    norm = normalize_section_number(section)
    if not norm:
        return []
    compact = norm.replace(" ", "")
    dashed = norm.replace(" ", "-")
    return list(
        dict.fromkeys(
            [
                f"Section {norm}",
                f"SECTION {norm}",
                f"SEC. {norm}",
                norm,
                dashed,
                compact,
            ]
        )
    )


def detect_page_section(page_text: str, current: str) -> str:
    """Update rolling section context while scanning a spec PDF page."""
    if not page_text:
        return current

    latest = current
    for pattern in (SECTION_HEADER_RE, SECTION_TITLE_LINE_RE):
        for match in pattern.finditer(page_text):
            group = match.group(1)
            norm = normalize_section_number(group)
            if norm:
                latest = norm
    return latest


def sections_from_filenames(filenames: list[str]) -> list[str]:
    """Infer MasterFormat sections referenced by uploaded file names."""
    found: list[str] = []
    seen: set[str] = set()
    for name in filenames:
        for match in SECTION_NUMBER_RE.finditer(name.replace("-", " ")):
            norm = f"{match.group(1)} {match.group(2)} {match.group(3)}"
            if norm not in seen:
                seen.add(norm)
                found.append(norm)
    return found
