from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pymupdf

from app.config import settings

ProgressCallback = Callable[[int, int], None]


def count_pdf_pages(path: Path) -> int:
    with pymupdf.open(str(path)) as doc:
        return doc.page_count


def _render_page_png(page: "pymupdf.Page", dpi: int = 200) -> bytes:
    """Rasterize a page to PNG bytes for OCR."""
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def iter_pdf_pages(path: Path) -> tuple[Iterator[tuple[int, str]], int, list[str]]:
    """
    Yield (page_number, text) one page at a time to limit memory on 2000+ page PDFs.
    Page numbers are 1-based.

    Text is extracted with PyMuPDF. Pages whose embedded text is too short to be
    meaningful (scanned images, vector-rendered text PyMuPDF can't read) are sent
    through vision OCR so the content still becomes searchable.
    """
    warnings: list[str] = []
    doc = pymupdf.open(str(path))
    total = doc.page_count
    limit = settings.max_pdf_pages

    if total > limit:
        warnings.append(
            f"{path.name}: PDF has {total:,} pages; indexing capped at {limit:,} pages. "
            "Raise MAX_PDF_PAGES in .env if needed."
        )

    pages_to_read = min(total, limit)
    min_chars = settings.pdf_ocr_min_chars
    ocr_budget = settings.max_ocr_pages

    def _gen() -> Iterator[tuple[int, str]]:
        ocr_used = 0
        for i in range(pages_to_read):
            page = doc.load_page(i)
            text = page.get_text("text") or ""

            # Scanned/image page (or text PyMuPDF couldn't read) → OCR fallback.
            if len(text.strip()) < min_chars and ocr_used < ocr_budget:
                try:
                    png = _render_page_png(page)
                except Exception:
                    png = b""
                if png:
                    from app.llm import ocr_image_bytes

                    ocr_text = ocr_image_bytes(png, "image/png")
                    if ocr_text and ocr_text.strip():
                        ocr_used += 1
                        text = f"{text}\n{ocr_text}".strip() if text.strip() else ocr_text

            yield i + 1, text
        doc.close()

    return _gen(), pages_to_read, warnings


def should_index_in_background(page_count: int) -> bool:
    return page_count >= settings.pdf_background_page_threshold
