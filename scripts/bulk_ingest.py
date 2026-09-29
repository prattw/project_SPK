"""Bulk-ingest a document folder into the local RAG index.

Usage:
    python scripts/bulk_ingest.py "DOCUMENTS for RAG"
    python scripts/bulk_ingest.py "/mnt/c/Users/CYRUS/Documents/Master Library" --corpus gov
    python scripts/bulk_ingest.py "/mnt/c/Users/CYRUS/Documents/Textbooks" --corpus textbook

- Walks the folder recursively and ingests every supported file.
- Skips files whose name is already in the index, so it is safe to re-run
  after an interruption.
- Skips duplicate filenames within the run (first one wins).
- Logs one line per file so progress is visible in the terminal.
- Optional --corpus tags every chunk from this run with a "corpus" field
  (e.g. "gov" vs "textbook") so multiple source collections stay
  distinguishable in the Document Library even though they share one
  vector index. Purely a label — retrieval still searches everything
  together; nothing is filtered by it today.

Stop the local API server before running this: ChromaDB does not support
two processes writing to the same persistent store.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ingest import discover_documents, ingest_path  # noqa: E402
from app.rag import get_rag  # noqa: E402


LOCK_FILE = Path("/tmp/spk_bulk_ingest.pid")


def acquire_lock() -> bool:
    """Refuse to run if another bulk ingest is alive — concurrent writes corrupt ChromaDB."""
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
            os.kill(old_pid, 0)  # raises if process is gone
            print(f"Another bulk ingest is already running (pid {old_pid}). Aborting.")
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # stale lock
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="Folder to walk and ingest recursively.")
    parser.add_argument(
        "--corpus",
        default="",
        help='Optional label stored on every chunk from this run (e.g. "gov", "textbook"). '
        "Purely informational — shows up in the Document Library, does not affect retrieval.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not acquire_lock():
        return 1

    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    paths = discover_documents(root)
    if not paths:
        print("No supported files found.")
        return 1

    extra_meta = {"corpus": args.corpus} if args.corpus else None

    rag = get_rag()
    already_indexed = set(rag.list_sources())
    print(f"Found {len(paths):,} supported files. "
          f"{len(already_indexed):,} sources already in the index.")
    if args.corpus:
        print(f"Tagging every indexed chunk from this run with corpus={args.corpus!r}.")

    seen_names: set[str] = set()
    done = skipped = failed = total_chunks = 0
    started = time.time()

    for i, path in enumerate(paths, 1):
        name = path.name
        prefix = f"[{i:>5}/{len(paths)}]"

        if name in already_indexed:
            skipped += 1
            print(f"{prefix} SKIP (already indexed): {name}", flush=True)
            continue
        if name in seen_names:
            skipped += 1
            print(f"{prefix} SKIP (duplicate filename): {name}", flush=True)
            continue
        seen_names.add(name)

        t0 = time.time()
        try:
            result = ingest_path(path, extra_meta=extra_meta)
            chunks = int(result.get("chunks_indexed", 0))
            total_chunks += chunks
            done += 1
            status = "OK" if chunks else "EMPTY"
            print(f"{prefix} {status}: {name} — {chunks:,} chunks "
                  f"in {time.time() - t0:.1f}s", flush=True)
            for w in result.get("warnings", []):
                print(f"        warning: {w}", flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted — re-run to resume where this left off.")
            return 130
        except Exception as exc:  # keep going on per-file failures
            failed += 1
            print(f"{prefix} FAIL: {name} — {exc}", flush=True)

    elapsed = (time.time() - started) / 60
    print(f"\nDone in {elapsed:.1f} min. "
          f"Indexed {done:,} files ({total_chunks:,} chunks), "
          f"skipped {skipped:,}, failed {failed:,}.")
    print(f"Total chunks now in index: {rag.document_count:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
