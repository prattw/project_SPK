"""Build citation labels and public URLs for indexed USACE / federal documents."""

from __future__ import annotations

import re
from urllib.parse import quote

# Official USACE publication portals (see README roadmap links).
USACE_PUBLICATIONS_SEARCH = "https://www.publications.usace.army.mil/SearchResults.aspx?search={query}"
USACE_LIBRARY = "https://www.usace.army.mil/Resources/Library/"
USACE_LIBRARY_PROGRAM = "https://www.usace.army.mil/Resources/Library/Library-Program/"
ERDC_LIBRARY = "https://www.erdc.usace.army.mil/Library.aspx"
USACE_GEOSPATIAL = "https://geospatial-usace.opendata.arcgis.com/"

_DOC_NUMBER_RE = re.compile(
    r"^(ER|EM|EP|EC|ECB|ETL|UFC|AR|PAM|FAR|DFARS|AFARS|PGI|TM)\s*[\d\-]+",
    re.IGNORECASE,
)


def publication_url(doc_number: str | None, source: str | None = None) -> str:
    """Return a public URL for a document number or filename."""
    if doc_number and doc_number.strip():
        query = quote(doc_number.strip())
        return USACE_PUBLICATIONS_SEARCH.format(query=query)

    name = (source or "").upper()
    if "UFC" in name or "UFGS" in name:
        return "https://www.wbdg.org/ffc/dod/unified-facilities-criteria-ufc"
    if "FAR" in name and "DFAR" not in name and "AFAR" not in name:
        return "https://www.acquisition.gov/browse/index/far"
    if "DFARS" in name:
        return "https://www.acquisition.gov/dfars"
    if "GEOSPATIAL" in name or "GIS" in name:
        return USACE_GEOSPATIAL
    if "ERDC" in name:
        return ERDC_LIBRARY
    return USACE_LIBRARY


def citation_label(
    source: str,
    doc_number: str | None = None,
    page: int | None = None,
) -> str:
    base = doc_number.strip() if doc_number else source
    if page is not None:
        return f"{base}, Page {page}"
    return base


def build_citation(
    source: str,
    doc_number: str | None = None,
    doc_type: str | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, str | int | None]:
    page = page_start
    if page_end and page_end != page_start:
        label = citation_label(source, doc_number, page_start)
        if page_start is not None:
            label = f"{doc_number or source}, Pages {page_start}–{page_end}"
    else:
        label = citation_label(source, doc_number, page)

    return {
        "source": source,
        "doc_number": doc_number or None,
        "doc_type": doc_type or None,
        "page": page,
        "page_end": page_end if page_end != page_start else None,
        "label": label,
        "url": publication_url(doc_number, source),
    }


def citations_from_chunks(chunks: list[dict]) -> list[dict]:
    """Deduplicate citations from retrieved chunks."""
    seen: set[str] = set()
    out: list[dict] = []

    for chunk in chunks:
        source = str(chunk.get("source", "unknown"))
        doc_number = chunk.get("doc_number")
        key = f"{doc_number or source}|{chunk.get('page_start')}|{chunk.get('page_end')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            build_citation(
                source=source,
                doc_number=doc_number,
                doc_type=chunk.get("doc_type"),
                page_start=chunk.get("page_start"),
                page_end=chunk.get("page_end"),
            )
        )
    return out
