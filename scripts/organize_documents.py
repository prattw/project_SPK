#!/usr/bin/env python3
"""Reorganize the document corpus into category folders (dry-run by default).

Reads the same filename conventions as ingestion, moves each file into its
proposed category folder, and quarantines exact duplicates in _duplicates/.

Usage:
    python scripts/build_catalog.py            # ALWAYS build the catalog first
    python scripts/organize_documents.py       # dry run — prints planned moves
    python scripts/organize_documents.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.doc_metadata import category_folder, infer_doc_metadata  # noqa: E402

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "DOCUMENTS for RAG"
SKIP_DIRS = {"_duplicates"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def organize(source: Path, apply: bool) -> None:
    if not source.exists():
        sys.exit(f"Source folder not found: {source}")

    files = [
        p for p in sorted(source.rglob("*"))
        if p.is_file()
        and not p.name.startswith(".")
        and not any(part in SKIP_DIRS for part in p.relative_to(source).parts)
    ]

    seen_hashes: dict[str, Path] = {}
    moves: list[tuple[Path, Path]] = []
    dupes: list[tuple[Path, Path]] = []

    for path in files:
        file_hash = sha256_file(path)
        meta = infer_doc_metadata(path.name)
        folder = category_folder(meta.get("category", "misc"))

        if file_hash in seen_hashes:
            dupes.append((path, seen_hashes[file_hash]))
            target = source / "_duplicates" / path.name
        else:
            seen_hashes[file_hash] = path
            target = source / folder / path.name

        if target.resolve() != path.resolve():
            # Avoid clobbering distinct files that share a name
            final = target
            n = 1
            while final.exists() and apply:
                final = target.with_stem(f"{target.stem}_{n}")
                n += 1
            moves.append((path, final))

    print(f"{'APPLYING' if apply else 'DRY RUN'} — {len(moves)} moves, "
          f"{len(dupes)} duplicates to _duplicates/\n")

    for src, dst in moves:
        rel_src = src.relative_to(source)
        rel_dst = dst.relative_to(source)
        print(f"  {rel_src}  ->  {rel_dst}")
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)

    if not apply:
        print("\nNothing moved. Re-run with --apply to perform these moves.")
    else:
        # Remove now-empty directories
        for d in sorted((p for p in source.rglob("*") if p.is_dir()), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        print("\nDone. Duplicates are quarantined in _duplicates/ — review then delete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    organize(args.source, args.apply)
