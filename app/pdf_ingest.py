from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from pypdf import PdfReader

from app.config import settings

ProgressCallback = Callable[[int, int], None]


def count_pdf_pages(path: Path) -> int:
    return len(PdfReader(str(path)).pages)


def iter_pdf_pages(path: Path) -> tuple[Iterator[tuple[int, str]], int, list[str]]:
    """
    Yield (page_number, text) one page at a time to limit memory on 2000+ page PDFs.
    Page numbers are 1-based.
    """
    warnings: list[str] = []
    reader = PdfReader(str(path))
    total = len(reader.pages)
    limit = settings.max_pdf_pages

    if total > limit:
        warnings.append(
            f"{path.name}: PDF has {total:,} pages; indexing capped at {limit:,} pages. "
            "Raise MAX_PDF_PAGES in .env if needed."
        )

    pages_to_read = min(total, limit)

    def _gen() -> Iterator[tuple[int, str]]:
        for i in range(pages_to_read):
            page_num = i + 1
            text = reader.pages[i].extract_text() or ""
            yield page_num, text

    return _gen(), pages_to_read, warnings


def should_index_in_background(page_count: int) -> bool:
    return page_count >= settings.pdf_background_page_threshold
