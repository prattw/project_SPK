"""Production library corpus ingest — split, stage, and index document batches."""

from __future__ import annotations

import io
import shutil
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.config import settings
from app.ingest import INGESTABLE_EXTENSIONS, discover_documents, ingest_path
from app.rag import get_rag

# Some PDFs (e.g. FAR.pdf) have deeply nested object trees that overflow the
# default recursion limit when pages are cloned into a new writer.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 50_000))

LIBRARY_INCOMING_DIR = "library-incoming"
DEFAULT_PART_PAGES = 500
DEFAULT_SPLIT_THRESHOLD = 1200

ProgressCallback = Callable[[str, int, int, str], None]
# args: phase ("split"|"ingest"), done, total, detail


@dataclass
class LibraryIngestReport:
    files_found: int = 0
    files_indexed: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    chunks_indexed: int = 0
    split_pdfs: int = 0
    purged_sources: int = 0
    indexed_files: list[str] = field(default_factory=list)
    failed_files: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_found": self.files_found,
            "files_indexed": self.files_indexed,
            "files_failed": self.files_failed,
            "files_skipped": self.files_skipped,
            "chunks_indexed": self.chunks_indexed,
            "split_pdfs": self.split_pdfs,
            "purged_sources": self.purged_sources,
            "indexed_files": self.indexed_files,
            "failed_files": self.failed_files,
            "warnings": self.warnings,
        }


def library_incoming_path() -> Path:
    path = settings.data_path / LIBRARY_INCOMING_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _skip_parent_parts(root: Path, path: Path) -> bool:
    rel_parts = path.relative_to(root).parts[:-1]
    return any(part.startswith("_") for part in rel_parts)


def _split_pdf(path: Path, part_pages: int) -> list[Path]:
    reader = PdfReader(str(path))
    total = len(reader.pages)
    parts: list[Path] = []
    for start in range(0, total, part_pages):
        end = min(start + part_pages, total)
        name = f"{path.stem}__p{start + 1:05d}-{end:05d}{path.suffix}"
        dest = path.with_name(name)
        parts.append(dest)
        if dest.exists():
            continue
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        with dest.open("wb") as fh:
            writer.write(fh)
    return parts


def split_oversized_pdfs(
    root: Path,
    *,
    part_pages: int = DEFAULT_PART_PAGES,
    threshold: int = DEFAULT_SPLIT_THRESHOLD,
    progress: ProgressCallback | None = None,
) -> int:
    """Split large PDFs into page-range parts; quarantine originals in _originals/."""
    pdfs = [
        p
        for p in sorted(root.rglob("*.pdf"))
        if not _skip_parent_parts(root, p)
    ]
    split_count = 0
    candidates = 0
    for p in pdfs:
        try:
            pages = len(PdfReader(str(p)).pages)
        except Exception as exc:
            if progress:
                progress("split", split_count, len(pdfs), f"SKIP unreadable: {p.name} ({exc})")
            continue
        if pages <= threshold:
            continue
        candidates += 1
        try:
            _split_pdf(p, part_pages)
        except Exception as exc:
            if progress:
                progress("split", split_count, candidates, f"FAIL split {p.name}: {exc}")
            continue
        quarantine = p.parent / "_originals"
        quarantine.mkdir(exist_ok=True)
        dest = quarantine / p.name
        if not dest.exists():
            p.rename(dest)
        split_count += 1
        if progress:
            progress("split", split_count, candidates, f"SPLIT {p.name} ({pages:,} pages)")
    return split_count


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def cleanup_quarantined_originals(root: Path) -> int:
    """Remove full PDFs quarantined after split to reclaim disk space."""
    quarantine = root / "_originals"
    if not quarantine.is_dir():
        return 0
    removed = 0
    for item in quarantine.iterdir():
        if item.is_file():
            item.unlink()
            removed += 1
    try:
        quarantine.rmdir()
    except OSError:
        pass
    return removed


def promote_to_data(path: Path, incoming_root: Path) -> Path:
    """Move a library-incoming file into data/ (one copy on disk).

    If the file was already staged during a prior partial run, drop the
    duplicate in library-incoming and index from data/.
    """
    settings.data_path.mkdir(parents=True, exist_ok=True)
    dest = settings.data_path / path.name
    if path.resolve() == dest.resolve():
        return dest

    if _is_under(path, incoming_root):
        if dest.exists():
            path.unlink()
            return dest
        shutil.move(str(path), str(dest))
        return dest

    if not dest.exists():
        shutil.copy2(path, dest)
    return dest


def stage_incoming_to_data(root: Path) -> list[Path]:
    """Promote incoming files into data/ without duplicating the full batch."""
    return [promote_to_data(path, root) for path in discover_documents(root)]


def purge_sources_matching(patterns: list[str]) -> int:
    """Remove indexed sources whose filename contains any pattern (case-insensitive)."""
    if not patterns:
        return 0
    lowered = [p.lower() for p in patterns if p.strip()]
    if not lowered:
        return 0
    rag = get_rag()
    removed = 0
    for source in rag.list_sources():
        name = source.lower()
        if any(pattern in name for pattern in lowered):
            removed += rag.delete_source(source)
    return removed


def run_library_ingest(
    corpus_root: Path | None = None,
    *,
    split_large_pdfs: bool = True,
    part_pages: int = DEFAULT_PART_PAGES,
    split_threshold: int = DEFAULT_SPLIT_THRESHOLD,
    purge_patterns: list[str] | None = None,
    progress: ProgressCallback | None = None,
) -> LibraryIngestReport:
    """Split oversized PDFs, stage files to data/, and index the corpus."""
    root = corpus_root or library_incoming_path()
    if not root.exists():
        raise FileNotFoundError(f"Corpus folder not found: {root}")

    report = LibraryIngestReport()

    if purge_patterns:
        report.purged_sources = purge_sources_matching(purge_patterns)

    if split_large_pdfs:
        report.split_pdfs = split_oversized_pdfs(
            root,
            part_pages=part_pages,
            threshold=split_threshold,
            progress=progress,
        )
        quarantined = cleanup_quarantined_originals(root)
        if quarantined and progress:
            progress(
                "split",
                report.split_pdfs,
                report.split_pdfs,
                f"Removed {quarantined} quarantined original(s) to free disk",
            )

    paths = discover_documents(root)
    report.files_found = len(paths)
    if not paths:
        return report

    total = len(paths)

    for i, source_path in enumerate(paths, 1):
        name = source_path.name
        if progress:
            progress("ingest", i - 1, total, f"Staging {name}")
        try:
            path = promote_to_data(source_path, root)
        except OSError as exc:
            report.files_failed += 1
            report.failed_files.append({"filename": name, "error": str(exc)})
            if progress:
                progress("ingest", i, total, f"Failed {name}: {exc}")
            continue

        if progress:
            progress("ingest", i - 1, total, f"Indexing {name}")

        def on_pages(done: int, pages_total: int) -> None:
            if progress:
                progress("ingest", i - 1, total, f"{name}: page {done:,}/{pages_total:,}")

        try:
            result = ingest_path(
                path,
                source_name=name,
                progress_callback=on_pages,
                extra_meta={"upload_origin": "library"},
            )
            chunks = int(result.get("chunks_indexed", 0))
            report.warnings.extend(list(result.get("warnings", [])))
            if result.get("files_processed"):
                report.files_indexed += 1
                report.chunks_indexed += chunks
                report.indexed_files.append(name)
            else:
                report.files_skipped += 1
                report.warnings.append(f"{name}: {result.get('message', 'no text indexed')}")
        except Exception as exc:
            report.files_failed += 1
            report.failed_files.append({"filename": name, "error": str(exc)})

        if progress:
            progress("ingest", i, total, f"Done {name}")

    return report


def save_incoming_upload(content: bytes, filename: str) -> Path:
    """Save an uploaded file under library-incoming/, preserving subpaths in the name."""
    incoming = library_incoming_path()
    safe = Path(filename).name
    if not safe or safe.startswith("."):
        raise ValueError("Invalid filename.")
    suffix = Path(safe).suffix.lower()
    if suffix not in INGESTABLE_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix}")
    dest = incoming / safe
    if dest.exists():
        stem = dest.stem
        ext = dest.suffix
        n = 2
        while dest.exists():
            dest = incoming / f"{stem} ({n}){ext}"
            n += 1
    dest.write_bytes(content)
    return dest


def extract_incoming_zip(content: bytes) -> list[str]:
    """Extract a zip archive into library-incoming/. Returns extracted filenames."""
    incoming = library_incoming_path()
    extracted: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename.startswith("__MACOSX/"):
                continue
            name = Path(info.filename).name
            if not name or name.startswith("."):
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in INGESTABLE_EXTENSIONS:
                continue
            dest = incoming / name
            if dest.exists():
                stem = dest.stem
                ext = dest.suffix
                n = 2
                while dest.exists():
                    dest = incoming / f"{stem} ({n}){ext}"
                    n += 1
            dest.write_bytes(zf.read(info))
            extracted.append(dest.name)
    return extracted
