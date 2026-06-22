from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.doc_metadata import infer_doc_metadata
from app.parsers.loaders import SUPPORTED_EXTENSIONS, STORE_ONLY_EXTENSIONS, load_document
from app.pdf_ingest import count_pdf_pages, iter_pdf_pages, should_index_in_background
from app.rag import get_rag

INGESTABLE_EXTENSIONS = SUPPORTED_EXTENSIONS | STORE_ONLY_EXTENSIONS


def _safe_filename(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^\w.\- ]", "_", base) or "upload"


ProgressCallback = Callable[[int, int], None]


def ingest_path(
    path: Path,
    source_name: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, int | str | list[str]]:
    suffix = path.suffix.lower()
    if suffix not in INGESTABLE_EXTENSIONS:
        return {
            "chunks_indexed": 0,
            "files_processed": [],
            "message": f"Unsupported file type: {suffix}",
            "warnings": [],
        }

    source = source_name or path.name
    rag = get_rag()
    rag.delete_source(source)
    doc_meta = infer_doc_metadata(source)

    if suffix == ".pdf":
        page_iter, pages_total, pdf_warnings = iter_pdf_pages(path)
        count, warnings, pages_indexed = rag.ingest_pdf_pages(
            source,
            page_iter,
            pages_total,
            progress_callback=progress_callback,
            initial_warnings=pdf_warnings,
            extra_meta=doc_meta,
        )
        message = (
            f"Indexed {pages_indexed:,} pages ({count:,} chunks) from {source}."
        )
        if warnings:
            message += " " + " ".join(warnings)
        return {
            "chunks_indexed": count,
            "files_processed": [source] if count else [],
            "message": message,
            "warnings": warnings,
            "pages_indexed": pages_indexed,
        }

    text = load_document(path)
    if not text or not text.strip():
        return {
            "chunks_indexed": 0,
            "files_processed": [],
            "message": "No extractable text from file.",
            "warnings": [],
        }

    count, warnings = rag.ingest_documents(
        [{"source": source, "text": text}], extra_meta=doc_meta
    )
    message = "File indexed successfully."
    if warnings:
        message += " " + " ".join(warnings)

    return {
        "chunks_indexed": count,
        "files_processed": [source] if count else [],
        "message": message,
        "warnings": warnings,
        "pages_indexed": 0,
    }


def ingest_file(path: Path, original_name: str | None = None) -> dict[str, int | str | list[str]]:
    return ingest_path(path, source_name=original_name)


def pdf_needs_background(path: Path) -> tuple[bool, int]:
    if path.suffix.lower() != ".pdf":
        return False, 0
    pages = count_pdf_pages(path)
    return should_index_in_background(pages), pages


def discover_documents(data_dir: Path | None = None) -> list[Path]:
    root = data_dir or settings.data_path
    if not root.exists():
        return []

    paths: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        # Skip helper folders (e.g. "_originals" quarantine) so split-aside
        # originals are not re-ingested whole.
        rel_parts = path.relative_to(root).parts[:-1]
        if any(part.startswith("_") for part in rel_parts):
            continue
        if path.suffix.lower() not in INGESTABLE_EXTENSIONS:
            continue
        paths.append(path)
    return paths


def ingest_directory(data_dir: Path | None = None) -> dict[str, int | list[str] | str]:
    paths = discover_documents(data_dir)
    if not paths:
        return {"chunks_indexed": 0, "files_processed": [], "message": "No supported files found.", "warnings": []}

    total_chunks = 0
    all_warnings: list[str] = []
    processed: list[str] = []

    for path in paths:
        result = ingest_path(path)
        total_chunks += int(result.get("chunks_indexed", 0))
        all_warnings.extend(list(result.get("warnings", [])))
        processed.extend(list(result.get("files_processed", [])))

    message = "Ingestion complete."
    if all_warnings:
        message += f" ({len(all_warnings)} notice(s).)"

    return {
        "chunks_indexed": total_chunks,
        "files_processed": processed,
        "message": message,
        "warnings": all_warnings,
    }


def save_upload(content: bytes, filename: str) -> Path:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    dest = settings.data_path / safe_name
    dest.write_bytes(content)
    return dest
