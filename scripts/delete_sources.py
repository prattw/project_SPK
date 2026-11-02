"""Delete specific document sources from the RAG index by filename.

Used to clear partial/truncated chunks left by files that failed, were
split, or hit the OCR page budget (MAX_OCR_PAGES) part way through, so a
follow-up bulk ingest can re-add them cleanly from scratch. bulk_ingest.py
only skips sources that already have *some* chunks indexed — it has no way
to tell "fully indexed" from "truncated", so a truncated file won't be
retried on a plain re-run until its old chunks are removed here.

Usage:
    # Exact filenames (as stored in the "source" chunk metadata):
    python scripts/delete_sources.py "usc19@119-88.pdf" "EP_5-1-5.pdf"
    python scripts/delete_sources.py --from-file /tmp/failed_sources.txt

    # Substring match against every indexed source (case-insensitive) --
    # handy when you don't know the exact filename, e.g. scanned files
    # that have a timestamp suffix appended by the scanner:
    python scripts/delete_sources.py --contains "Corporate_Finance" --dry-run
    python scripts/delete_sources.py --contains "Corporate_Finance"

Always try --dry-run first with --contains: it only lists the matching
source names and chunk counts without deleting anything, so you can confirm
the match is exactly the set of files you intend to remove.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag import get_rag  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*", help="Exact source filenames to delete.")
    ap.add_argument("--from-file", help="File with one exact source name per line.")
    ap.add_argument(
        "--contains",
        action="append",
        default=[],
        metavar="SUBSTRING",
        help="Delete every indexed source whose filename contains this substring "
        "(case-insensitive). Repeatable. Combine with --dry-run to preview matches first.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be deleted without actually deleting it.",
    )
    args = ap.parse_args()

    names: list[str] = list(args.sources)
    if args.from_file:
        names.extend(
            line.strip() for line in Path(args.from_file).read_text().splitlines()
            if line.strip()
        )

    rag = get_rag()

    if args.contains:
        needles = [s.lower() for s in args.contains]
        matched = sorted(
            source for source in rag.list_sources()
            if any(needle in source.lower() for needle in needles)
        )
        if not matched:
            print(f"No indexed sources match {args.contains!r}.")
        names.extend(matched)

    names = sorted(set(names))
    if not names:
        print("No source names given (use positional names, --from-file, and/or --contains).")
        return 1

    if args.dry_run:
        print(f"Would delete {len(names)} source(s):")
        for name in names:
            print(f"  {name}")
        print("\n(dry run -- nothing was deleted; re-run without --dry-run to delete)")
        return 0

    total = 0
    for name in names:
        removed = rag.delete_source(name)
        total += removed
        print(f"{'deleted' if removed else 'not found':>10}: {name} ({removed} chunks)")
    print(f"\nRemoved {total:,} chunks from {len(names)} source name(s).")
    print(f"Index now holds {rag.document_count:,} chunks.")
    print(
        "These file(s) will now be re-processed on the next bulk_ingest.py run "
        "(they're no longer recorded in the index)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
