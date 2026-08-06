#!/usr/bin/env python3
"""Bulk-upload a folder tree to Project SPK's library-incoming via zip batches.

Filters to supported/store-only extensions, skips excluded files, and splits
the result into zip batches under a size cap so uploads stay well under the
server's MAX_UPLOAD_MB and any single request stays fast/retryable.

Usage:
  export SPK_URL="https://projectspk-production.up.railway.app"
  export SPK_TOKEN="your-admin-bearer-token"

  python3 scripts/zip_upload_library.py "/path/to/folder" \
    --exclude "(CUI) Policy Alert Summary 14 FEB 2025.pdf" \
    --exclude "CUI Doc - PAM.pdf"

  # Dry run (no upload, just show what would happen):
  python3 scripts/zip_upload_library.py "/path/to/folder" --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

# ZIP format allows multiple entries with the same name (harmless here — the
# server's extract_incoming_zip disambiguates by destination path on write).
warnings.filterwarnings("ignore", message="Duplicate name.*", category=UserWarning)

# Mirrors app/parsers/loaders.py SUPPORTED_EXTENSIONS | STORE_ONLY_EXTENSIONS.
INGESTABLE_EXTENSIONS = {
    ".txt", ".md", ".pdf", ".docx", ".xlsx", ".csv", ".pptx",
    ".xer", ".xml", ".ifc", ".gltf", ".glb",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".rvt", ".dwg", ".dxf", ".nwd", ".nwc", ".fbx", ".obj", ".3dm", ".msg",
}

DEFAULT_MAX_ZIP_MB = 60
UPLOAD_TIMEOUT_SECONDS = 600
UPLOAD_MAX_RETRIES = 3


def discover_files(root: Path, excludes: set[str]) -> list[Path]:
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() not in INGESTABLE_EXTENSIONS:
            continue
        if p.name in excludes:
            continue
        files.append(p)
    return files


def batch_files(files: list[Path], max_bytes: int) -> list[list[Path]]:
    batches: list[list[Path]] = []
    current: list[Path] = []
    current_size = 0
    for f in files:
        size = f.stat().st_size
        if current and current_size + size > max_bytes:
            batches.append(current)
            current = []
            current_size = 0
        current.append(f)
        current_size += size
    if current:
        batches.append(current)
    return batches


def build_zip(files: list[Path]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)
    return buf.getvalue()


def upload_zip(base_url: str, token: str, data: bytes, label: str) -> dict:
    """Upload via curl (matches the rest of the admin tooling — avoids macOS
    Python SSL cert issues) using a temp file for the multipart body.

    Retries on timeout/transient failure — safe because a failed/timed-out
    attempt that never reached the server leaves nothing in library-incoming
    to duplicate (confirmed: server only responds 200 after the zip is fully
    extracted and written).
    """
    import tempfile
    import time

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        last_error: Exception | None = None
        for attempt in range(1, UPLOAD_MAX_RETRIES + 1):
            proc = subprocess.run(
                [
                    "curl", "-sS", "--max-time", str(UPLOAD_TIMEOUT_SECONDS),
                    "-w", "\n__HTTP_STATUS__:%{http_code}",
                    "-X", "POST",
                    f"{base_url.rstrip('/')}/admin/library/upload-zip",
                    "-H", f"Authorization: Bearer {token}",
                    "-F", f"file=@{tmp_path};filename={label};type=application/zip",
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                last_error = RuntimeError(proc.stderr or proc.stdout or "curl failed")
                print(f"  attempt {attempt}/{UPLOAD_MAX_RETRIES} failed: {last_error}; retrying...")
                time.sleep(5)
                continue
            body, _, status = proc.stdout.rpartition("\n__HTTP_STATUS__:")
            code = int(status or "0")
            if code >= 400:
                last_error = RuntimeError(f"HTTP {code}: {body.strip()}")
                print(f"  attempt {attempt}/{UPLOAD_MAX_RETRIES} failed: {last_error}; retrying...")
                time.sleep(5)
                continue
            return json.loads(body)
        raise last_error or RuntimeError("upload failed after retries")
    finally:
        os.unlink(tmp_path)


def fetch_incoming_names(base_url: str, token: str) -> set[str]:
    """Names already sitting in library-incoming (e.g. from an interrupted prior run)."""
    proc = subprocess.run(
        [
            "curl", "-sS", "--max-time", "30",
            f"{base_url.rstrip('/')}/admin/library/incoming",
            "-H", f"Authorization: Bearer {token}",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return set()
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return set()
    return {f["filename"] for f in data.get("files", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="Folder to upload (recursive)")
    parser.add_argument("--url", default=os.environ.get("SPK_URL", ""))
    parser.add_argument("--token", default=os.environ.get("SPK_TOKEN", ""))
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Exact filename to exclude (repeatable)",
    )
    parser.add_argument("--max-zip-mb", type=int, default=DEFAULT_MAX_ZIP_MB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-already-incoming", action="store_true",
        help="Query /admin/library/incoming first and skip filenames already queued "
        "(safe to resume an interrupted run without re-uploading/duplicating files)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    excludes = set(args.exclude)
    if args.skip_already_incoming and args.url and args.token:
        already = fetch_incoming_names(args.url, args.token)
        if already:
            print(f"Skipping {len(already)} file(s) already in library-incoming.")
            excludes |= already
    files = discover_files(root, excludes)
    if not files:
        print("No ingestible files found.", file=sys.stderr)
        return 1

    total_bytes = sum(f.stat().st_size for f in files)
    batches = batch_files(files, args.max_zip_mb * 1024 * 1024)

    print(f"Found {len(files)} ingestible file(s), {total_bytes / (1024**3):.2f} GB total")
    print(f"Excluded {len(excludes)} file(s) by name: {sorted(excludes)}")
    print(f"Split into {len(batches)} zip batch(es) (max {args.max_zip_mb} MB each)")

    if args.dry_run:
        for i, batch in enumerate(batches, 1):
            size = sum(f.stat().st_size for f in batch) / (1024 * 1024)
            print(f"  Batch {i}: {len(batch)} files, {size:.1f} MB")
        return 0

    if not args.url or not args.token:
        print("Set --url/--token or SPK_URL/SPK_TOKEN env vars to actually upload.", file=sys.stderr)
        return 1

    for i, batch in enumerate(batches, 1):
        label = f"batch_{i:03d}.zip"
        print(f"Uploading batch {i}/{len(batches)} ({len(batch)} files) as {label} ...")
        data = build_zip(batch)
        result = upload_zip(args.url, args.token, data, label)
        print(f"  -> {result.get('message')}")

    print("\nAll batches uploaded. Check the queue:")
    print(f"  curl -s {args.url}/admin/library/incoming -H \"Authorization: Bearer $SPK_TOKEN\"")
    print("Then start ingest:")
    print(f"  curl -s -X POST {args.url}/admin/library/ingest -H \"Authorization: Bearer $SPK_TOKEN\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
