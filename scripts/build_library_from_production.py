#!/usr/bin/env python3
"""Build catalog + Document Library HTML from the live production index.

Usage:
  export SPK_URL="https://projectspk-production.up.railway.app"
  export SPK_TOKEN="your-bearer-token"
  python3 scripts/build_library_from_production.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog" / "catalog.json"


def fetch_library_documents(base_url: str, token: str) -> list[dict]:
    url = f"{base_url.rstrip('/')}/files"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        doc
        for doc in data.get("documents", [])
        if (doc.get("upload_origin") or "").lower() == "library"
    ]


def write_catalog(documents: list[dict]) -> None:
    entries = []
    for doc in sorted(documents, key=lambda d: (d.get("source") or "").lower()):
        filename = doc.get("source") or ""
        if not filename:
            continue
        entries.append(
            {
                "filename": filename,
                "doc_number": doc.get("doc_number") or "",
                "doc_type": doc.get("doc_type") or "",
                "title": doc.get("title") or "",
                "category": doc.get("category") or "",
                "part": doc.get("part") or "",
            }
        )
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    catalog = {
        "generated": datetime.now(tz=timezone.utc).isoformat(),
        "source": "production:/files",
        "file_count": len(entries),
        "entries": entries,
    }
    CATALOG.write_text(json.dumps(catalog, indent=1), encoding="utf-8")
    print(f"Wrote {len(entries)} library entries to {CATALOG}")


def main() -> int:
    base_url = os.environ.get("SPK_URL", "").strip()
    token = os.environ.get("SPK_TOKEN", "").strip()
    if not base_url or not token:
        print("Set SPK_URL and SPK_TOKEN", file=sys.stderr)
        return 1

    documents = fetch_library_documents(base_url, token)
    if not documents:
        print("No library documents returned from production.", file=sys.stderr)
        return 1

    write_catalog(documents)
    subprocess.check_call([sys.executable, str(ROOT / "scripts" / "build_library_html.py")])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
