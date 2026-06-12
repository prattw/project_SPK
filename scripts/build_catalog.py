#!/usr/bin/env python3
"""Catalog the document corpus: metadata, duplicates, and proposed structure.

Usage:
    python scripts/build_catalog.py                      # uses ./DOCUMENTS for RAG
    python scripts/build_catalog.py --source /path/dir   # custom folder
    python scripts/build_catalog.py --deep               # also read PDF page counts/titles

Outputs (in ./catalog/, gitignored):
    catalog.json     full machine-readable catalog
    catalog.csv      spreadsheet-friendly view
    duplicates.txt   files with identical content (by SHA-256)
    structure.txt    proposed folder organization by category
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.doc_metadata import category_folder, infer_doc_metadata  # noqa: E402

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "DOCUMENTS for RAG"
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "catalog"

SKIP_NAMES = {".DS_Store"}


def sha256_file(path: Path, block_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def pdf_deep_info(path: Path) -> dict[str, str | int]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        info: dict[str, str | int] = {"pages": len(reader.pages)}
        meta = reader.metadata
        if meta and meta.title:
            info["pdf_title"] = str(meta.title)[:200]
        return info
    except Exception as exc:  # corrupt/encrypted PDFs shouldn't kill the run
        return {"pdf_error": str(exc)[:120]}


def build(source: Path, out_dir: Path, deep: bool) -> None:
    if not source.exists():
        sys.exit(f"Source folder not found: {source}")

    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    by_hash: dict[str, list[str]] = defaultdict(list)

    files = [
        p for p in sorted(source.rglob("*"))
        if p.is_file() and p.name not in SKIP_NAMES and not p.name.startswith(".")
    ]
    print(f"Scanning {len(files)} files in {source} ...")

    for i, path in enumerate(files, 1):
        rel = str(path.relative_to(source))
        stat = path.stat()
        file_hash = sha256_file(path)
        by_hash[file_hash].append(rel)

        meta = infer_doc_metadata(path.name)
        entry = {
            "relative_path": rel,
            "filename": path.name,
            "ext": path.suffix.lower(),
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "sha256": file_hash,
            "proposed_folder": category_folder(meta.get("category", "misc")),
            **meta,
        }
        if deep and path.suffix.lower() == ".pdf":
            entry.update(pdf_deep_info(path))

        entries.append(entry)
        if i % 100 == 0:
            print(f"  {i}/{len(files)}")

    # catalog.json
    catalog = {
        "generated": datetime.now(tz=timezone.utc).isoformat(),
        "source": str(source),
        "file_count": len(entries),
        "total_bytes": sum(e["size_bytes"] for e in entries),
        "entries": entries,
    }
    (out_dir / "catalog.json").write_text(json.dumps(catalog, indent=1), encoding="utf-8")

    # catalog.csv
    fieldnames = [
        "relative_path", "filename", "ext", "doc_number", "doc_type",
        "category", "proposed_folder", "title", "size_bytes", "modified", "sha256",
    ]
    if deep:
        fieldnames += ["pages", "pdf_title", "pdf_error"]
    with (out_dir / "catalog.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(entries)

    # duplicates.txt
    dupes = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    with (out_dir / "duplicates.txt").open("w", encoding="utf-8") as fh:
        if not dupes:
            fh.write("No duplicate files found.\n")
        for h, paths in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
            fh.write(f"\n{len(paths)} copies (sha256 {h[:12]}…):\n")
            for p in paths:
                fh.write(f"  {p}\n")

    # structure.txt
    by_folder: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_folder[e["proposed_folder"]].append(e)
    with (out_dir / "structure.txt").open("w", encoding="utf-8") as fh:
        fh.write("Proposed folder structure (run scripts/organize_documents.py --apply to apply)\n")
        fh.write("=" * 70 + "\n")
        for folder in sorted(by_folder):
            items = by_folder[folder]
            size_mb = sum(e["size_bytes"] for e in items) / (1024 * 1024)
            fh.write(f"\n{folder}/   ({len(items)} files, {size_mb:,.0f} MB)\n")
            for e in sorted(items, key=lambda x: x["filename"])[:8]:
                num = f"[{e['doc_number']}] " if e.get("doc_number") else ""
                fh.write(f"    {num}{e['filename']}\n")
            if len(items) > 8:
                fh.write(f"    … and {len(items) - 8} more\n")

    # Summary to stdout
    print(f"\nCatalog written to {out_dir}/")
    print(f"  Files: {len(entries)}  ({catalog['total_bytes'] / (1024**2):,.0f} MB)")
    dupe_files = sum(len(p) - 1 for p in dupes.values())
    print(f"  Duplicate copies: {dupe_files} (see duplicates.txt)")
    print("\n  By category:")
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        counts[e.get("category", "misc")] += 1
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {cat:24s} {n}")
    untyped = sum(1 for e in entries if e["ext"] not in
                  {".pdf", ".docx", ".xlsx", ".txt", ".md", ".xml", ".xer", ".ifc", ".gltf", ".glb"})
    if untyped:
        print(f"\n  Note: {untyped} file(s) have store-only types (.msg, images, etc.)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--deep", action="store_true", help="Read PDF page counts and embedded titles (slower)")
    args = parser.parse_args()
    build(args.source, args.out, args.deep)
