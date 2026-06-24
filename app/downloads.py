"""Resolve indexed document files on disk and build download / link URLs."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import quote

from app.config import settings
from app.usace_dates import load_official_publications, official_fields


def download_url(source: str) -> str:
    safe = Path(source).name
    return f"/download/{quote(safe, safe='')}"


def _is_under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_data_file(source: str) -> Path | None:
    """Locate an indexed file under the data directory by its source name."""
    if not source:
        return None

    safe = Path(source).name
    if not safe or safe != source.strip():
        return None

    root = settings.data_path
    if not root.exists():
        return None

    root_resolved = root.resolve()
    candidates: list[Path] = []

    direct = root / safe
    if direct.is_file() and _is_under_root(direct, root_resolved):
        candidates.append(direct)

    for path in root.rglob(safe):
        if path.is_file() and _is_under_root(path, root_resolved):
            if path not in candidates:
                candidates.append(path)

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    return sorted(candidates, key=lambda p: (len(p.parts), -p.stat().st_mtime))[0]


def guess_media_type(path: Path) -> str:
    media_type, _ = mimetypes.guess_type(path.name)
    return media_type or "application/octet-stream"


def has_official_publication(
    doc_number: str | None,
    official_lookup: dict | None = None,
) -> bool:
    if not doc_number:
        return False
    table = official_lookup if official_lookup is not None else load_official_publications()
    return official_fields(doc_number, table) is not None


def document_link_url(
    doc_number: str | None,
    source: str | None = None,
    *,
    upload_origin: str | None = None,
    official_lookup: dict | None = None,
) -> str:
    """Return a USACE portal URL or a local download link for a document."""
    from app.citations import publication_url

    if upload_origin == "user" and source:
        return download_url(source)

    if doc_number and has_official_publication(doc_number, official_lookup):
        return publication_url(doc_number, source)

    if source and resolve_data_file(source):
        return download_url(source)

    return publication_url(doc_number, source)
