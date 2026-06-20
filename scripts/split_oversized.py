"""Split oversized PDFs into page-range parts so they index fully.

The RAG ingester caps each file at a fixed number of chunks/pages, so very
large PDFs (e.g. 4,000+ page US Code titles) get truncated. Splitting them
into smaller parts gives each part its own budget and keeps embedding bursts
small (gentler on API rate limits).

Usage:
    python scripts/split_oversized.py "DOCUMENTS for RAG" --part-pages 500 --threshold 1200

For each PDF whose page count exceeds --threshold:
  - writes parts named  <stem>__p00001-00500.pdf  in the same folder
  - moves the original into a sibling "_originals/" folder, which the
    ingester ignores (folders starting with "_" are skipped)

Splitting is pure file I/O — no API calls, no cost. Run the bulk ingest
afterward to index the new parts. Idempotent: parts that already exist are
not recreated, and files already inside an "_originals" folder are skipped.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Some PDFs (e.g. FAR.pdf) have deeply nested object trees that overflow the
# default recursion limit when pages are cloned into a new writer.
sys.setrecursionlimit(50_000)

from pypdf import PdfReader, PdfWriter  # noqa: E402


def split_one(path: Path, part_pages: int) -> list[Path]:
    reader = PdfReader(str(path))
    total = len(reader.pages)
    parts: list[Path] = []
    for start in range(0, total, part_pages):
        end = min(start + part_pages, total)
        # 1-based, zero-padded page range in the name for clean sorting/citations.
        name = f"{path.stem}__p{start + 1:05d}-{end:05d}{path.suffix}"
        dest = path.with_name(name)
        parts.append(dest)
        if dest.exists():
            continue
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        with open(dest, "wb") as fh:
            writer.write(fh)
    return parts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("directory")
    ap.add_argument("--part-pages", type=int, default=500)
    ap.add_argument("--threshold", type=int, default=1200,
                    help="Only split PDFs with more pages than this.")
    args = ap.parse_args()

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    pdfs = [p for p in sorted(root.rglob("*.pdf"))
            if not any(part.startswith("_") for part in p.relative_to(root).parts[:-1])]
    print(f"Scanning {len(pdfs):,} PDFs for files over {args.threshold:,} pages...")

    split_count = 0
    for p in pdfs:
        try:
            pages = len(PdfReader(str(p)).pages)
        except Exception as exc:
            print(f"  SKIP (unreadable): {p.name} — {exc}", flush=True)
            continue
        if pages <= args.threshold:
            continue

        t0 = time.time()
        try:
            parts = split_one(p, args.part_pages)
        except Exception as exc:
            print(f"  FAIL splitting {p.name}: {exc}", flush=True)
            continue

        # Move original into the ignored "_originals" quarantine.
        quarantine = p.parent / "_originals"
        quarantine.mkdir(exist_ok=True)
        p.rename(quarantine / p.name)

        split_count += 1
        print(f"SPLIT: {p.name} ({pages:,} pages) -> {len(parts)} parts "
              f"in {time.time() - t0:.1f}s", flush=True)

    print(f"\nDone. Split {split_count} oversized PDF(s) into {args.part_pages}-page parts.")
    print("Originals moved to '_originals/' folders (ignored by the ingester).")
    print("Next: re-run scripts/bulk_ingest.py to index the new parts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
