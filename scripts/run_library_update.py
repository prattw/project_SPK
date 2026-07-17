#!/usr/bin/env python3
"""Run a full library update on the server or locally (split + index).

Usage:
    python scripts/run_library_update.py
    python scripts/run_library_update.py "/app/data/library-incoming"
    python scripts/run_library_update.py --purge UFC "AR 25-50"

On Railway, upload files to /app/data/library-incoming first (via the admin
API or volume), then run this script in a one-off shell while the app is idle,
or use POST /admin/library/ingest from the running app instead.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.library_ingest import library_incoming_path, run_library_ingest  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "corpus",
        nargs="?",
        default=str(library_incoming_path()),
        help="Folder to ingest (default: data/library-incoming)",
    )
    ap.add_argument(
        "--purge",
        nargs="*",
        default=[],
        help="Remove indexed sources matching these substrings before ingest.",
    )
    ap.add_argument("--no-split", action="store_true", help="Skip splitting oversized PDFs.")
    args = ap.parse_args()

    root = Path(args.corpus).expanduser().resolve()
    print(f"Library ingest from: {root}")

    def progress(phase: str, done: int, total: int, detail: str) -> None:
        if phase == "ingest" and total:
            print(f"  [{done}/{total}] {detail}", flush=True)
        elif phase == "split":
            print(f"  {detail}", flush=True)

    report = run_library_ingest(
        root,
        split_large_pdfs=not args.no_split,
        purge_patterns=args.purge or None,
        progress=progress,
    )
    print(json.dumps(report.to_dict(), indent=2))
    return 1 if report.files_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
