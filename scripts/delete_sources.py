"""Delete specific document sources from the RAG index by filename.

Used to clear partial/truncated chunks left by files that failed or were
split, so a follow-up bulk ingest can re-add them cleanly.

Usage:
    python scripts/delete_sources.py "usc19@119-88.pdf" "EP_5-1-5.pdf"
    python scripts/delete_sources.py --from-file /tmp/failed_sources.txt

Source names are the bare filenames as stored in the index (the "source"
metadata), e.g. "usc19@119-88.pdf".
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag import get_rag  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*", help="Source filenames to delete.")
    ap.add_argument("--from-file", help="File with one source name per line.")
    args = ap.parse_args()

    names: list[str] = list(args.sources)
    if args.from_file:
        names.extend(
            line.strip() for line in Path(args.from_file).read_text().splitlines()
            if line.strip()
        )
    names = sorted(set(names))
    if not names:
        print("No source names given.")
        return 1

    rag = get_rag()
    total = 0
    for name in names:
        removed = rag.delete_source(name)
        total += removed
        print(f"{'deleted' if removed else 'not found':>10}: {name} ({removed} chunks)")
    print(f"\nRemoved {total:,} chunks from {len(names)} source name(s).")
    print(f"Index now holds {rag.document_count:,} chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
