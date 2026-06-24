#!/usr/bin/env python3
"""Generate hard-coded Document Library HTML from catalog/catalog.json.

Uses official publication dates from static/usace_publication_dates.json when
a document number matches the USACE Publications website.

Usage:
    python scripts/build_library_html.py

Updates static/index.html between LIBRARY_LIST_START / LIBRARY_LIST_END markers.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.downloads import document_link_url  # noqa: E402
from app.doc_metadata import (  # noqa: E402
    USER_UPLOAD_SOURCES,
    display_title,
    enrich_library_fields,
    extract_part,
    infer_doc_metadata,
)
from app.usace_dates import load_official_publications, official_fields  # noqa: E402

CATALOG = ROOT / "catalog" / "catalog.json"
INDEX = ROOT / "static" / "index.html"
START = "<!-- LIBRARY_LIST_START -->"
END = "<!-- LIBRARY_LIST_END -->"


def _entry_html(entry: dict, official_lookup: dict) -> str | None:
    filename = entry.get("filename") or ""
    if filename in USER_UPLOAD_SOURCES:
        return None

    meta = infer_doc_metadata(filename)
    meta.update(
        {
            k: v
            for k, v in entry.items()
            if k in ("doc_number", "doc_type", "title", "category", "part")
            and v
        }
    )

    doc_number = meta.get("doc_number") or ""
    official = official_fields(doc_number, official_lookup)

    if official:
        title = official["display_title"]
        date_label = official["date_label"]
        date_year = official["date_year"]
        url = document_link_url(doc_number, filename, official_lookup=official_lookup)
    else:
        modified = entry.get("modified")
        if modified:
            meta["updated_at"] = modified
        fields = enrich_library_fields(filename, meta)
        title = fields.get("display_title") or meta.get("title") or filename
        date_label = fields.get("date_label") or "published"
        date_year = fields.get("date_year") or "—"
        url = document_link_url(doc_number, filename, official_lookup=official_lookup)

    label = doc_number or filename
    part = extract_part(filename)
    part_html = f', <span class="lib-part">{html.escape(part)}</span>' if part else ""
    download_attr = ' download' if url.startswith("/download/") else ""
    target_attr = "" if url.startswith("/download/") else ' target="_blank" rel="noopener"'
    return (
        f'<div class="library-item">'
        f'<a href="{html.escape(url)}"{target_attr}{download_attr}>{html.escape(label)}</a>'
        f", {html.escape(title)}"
        f"{part_html}"
        f'<span class="lib-updated">, {date_label}: {html.escape(str(date_year))}</span>'
        f"</div>"
    )


def _sort_key(entry: dict) -> tuple[str, str]:
    meta = infer_doc_metadata(entry.get("filename") or "")
    return (
        (meta.get("doc_number") or entry.get("filename") or "").upper(),
        entry.get("filename") or "",
    )


def build_fragment() -> str:
    if not CATALOG.exists():
        sys.exit(f"Catalog not found: {CATALOG}\nRun: python scripts/build_catalog.py")

    official_lookup = load_official_publications()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = catalog.get("entries") or []
    items: list[str] = []
    matched = 0

    for entry in sorted(entries, key=_sort_key):
        meta = infer_doc_metadata(entry.get("filename") or "")
        if official_fields(meta.get("doc_number"), official_lookup):
            matched += 1
        block = _entry_html(entry, official_lookup)
        if block:
            items.append(f"            {block}")

    if not items:
        return '            <div class="library-list-empty">No library documents cataloged yet.</div>'

    print(f"Official USACE dates applied to {matched} of {len(items)} library entries.")
    return "\n".join(items)


def patch_index(fragment: str) -> None:
    text = INDEX.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"Markers not found in {INDEX}")

    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    updated = f"{before}{START}\n{fragment}\n          {END}{after}"
    INDEX.write_text(updated, encoding="utf-8")


def main() -> None:
    fragment = build_fragment()
    patch_index(fragment)
    count = fragment.count("library-item")
    print(f"Wrote {count} library entries to {INDEX}")


if __name__ == "__main__":
    main()
