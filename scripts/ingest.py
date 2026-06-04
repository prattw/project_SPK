#!/usr/bin/env python3
"""CLI: index all supported files in the data directory."""

import sys
from pathlib import Path

# Allow running without installing as a package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.ingest import ingest_directory  # noqa: E402


def main() -> None:
    result = ingest_directory()
    print(result["message"])
    if result.get("files_processed"):
        print("Files:", ", ".join(result["files_processed"]))
    print(f"Chunks indexed: {result['chunks_indexed']}")


if __name__ == "__main__":
    main()
