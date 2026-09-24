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

  # Send only what is not in the database yet — for a folder where most of the
  # contents have already been uploaded at some point:
  python3 scripts/zip_upload_library.py "~/Documents/Master Library" \
    --skip-already-indexed --ingest

  # File an entire folder on one Document Library index page, regardless of
  # what the filenames look like:
  python3 scripts/zip_upload_library.py "~/Documents/Project SPK folder/Master Library" \
    --group discipline-knowledge

  # On Windows use `python`, set the vars with `$env:SPK_URL = "..."`, and quote
  # the folder (these paths contain spaces and parentheses):
  #   python scripts\\zip_upload_library.py "C:\\Users\\YOU\\Documents\\Project SPK folder\\Master Library" --group discipline-knowledge

  # Dry run (no upload, just show what would happen):
  python3 scripts/zip_upload_library.py "/path/to/folder" --dry-run
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
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
DRY_RUN_LIST_LIMIT = 60

# Railway answers 502 while a new container is starting, so a deploy landing
# mid-upload is something to wait out rather than report as a failure.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
API_MAX_ATTEMPTS = 6
API_RETRY_SECONDS = 10


class ApiError(RuntimeError):
    """A request that never came back usable, after waiting the server out."""

# Mirrors app/library_groups.py GROUP_ORDER.
LIBRARY_GROUPS = ("engineering", "contracting-law", "discipline-knowledge", "miscellaneous")

# Mirrors the part names app/library_ingest.py _split_pdf writes.
PART_NAME_RE = re.compile(r"^(?P<stem>.+)__p\d{5}-\d{5}$")


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


def upload_zip(base_url: str, token: str, data: bytes, label: str, group: str = "") -> dict:
    """Upload via curl (matches the rest of the admin tooling — avoids macOS
    Python SSL cert issues) using a temp file for the multipart body.

    Retries on timeout/transient failure — safe because a failed/timed-out
    attempt that never reached the server leaves nothing in library-incoming
    to duplicate (confirmed: server only responds 200 after the zip is fully
    extracted and written).
    """
    import tempfile
    import time
    from urllib.parse import quote

    endpoint = f"{base_url.rstrip('/')}/admin/library/upload-zip"
    if group:
        endpoint += f"?group={quote(group)}"

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
                    endpoint,
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
                # A rejected zip or a bad token is rejected just as firmly next
                # time; only a server that is down is worth waiting out.
                if code not in TRANSIENT_STATUSES:
                    raise ApiError(f"HTTP {code}: {body.strip()[:300]}")
                last_error = RuntimeError(f"HTTP {code}: {body.strip()[:200]}")
                print(f"  attempt {attempt}/{UPLOAD_MAX_RETRIES} failed: {last_error}; retrying...")
                time.sleep(5)
                continue
            return json.loads(body)
        raise last_error or RuntimeError("upload failed after retries")
    finally:
        os.unlink(tmp_path)


def api_json(base_url: str, token: str, path: str, method: str = "GET", body: dict | None = None) -> dict:
    """One JSON call via curl, waiting out a server that is down or restarting.

    A redeploy takes the app away for a few seconds and the proxy answers 502
    with an HTML page in the meantime, so the status code is read rather than
    inferred from whether the body happens to parse as JSON.
    """
    cmd = [
        "curl", "-sS", "--max-time", "120",
        "-w", "\n__HTTP_STATUS__:%{http_code}",
        "-X", method,
        f"{base_url.rstrip('/')}{path}",
        "-H", f"Authorization: Bearer {token}",
    ]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]

    problem = "request failed"
    for attempt in range(1, API_MAX_ATTEMPTS + 1):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            payload, _, status = proc.stdout.rpartition("\n__HTTP_STATUS__:")
            code = int(status or 0)
            if code not in TRANSIENT_STATUSES:
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    raise ApiError(f"HTTP {code} from {path}: {payload.strip()[:300]}") from None
                if code >= 400:
                    raise ApiError(f"HTTP {code} from {path}: {data.get('detail', data)}")
                return data
            problem = f"HTTP {code} from {path}"
        else:
            problem = (proc.stderr or "no response").strip()

        if attempt < API_MAX_ATTEMPTS:
            print(f"  {problem} — the server may be restarting; retrying in {API_RETRY_SECONDS}s")
            time.sleep(API_RETRY_SECONDS)

    raise ApiError(f"{problem}, and it did not recover after {API_MAX_ATTEMPTS} attempts.")


def run_ingest(base_url: str, token: str) -> int:
    """Start the ingest and follow it to the end.

    Indexing a large folder runs for a long time, so the progress line matters more
    than the return value — an ingest left unwatched looks identical to one that
    died.
    """
    started = api_json(base_url, token, "/admin/library/ingest", method="POST")
    job_id = started.get("job_id")
    print(f"\n{started.get('message', 'Ingest started.')}")
    if not job_id:
        return 1

    last = ""
    while True:
        time.sleep(5)
        try:
            job = api_json(base_url, token, f"/jobs/{job_id}")
        except ApiError as exc:
            # A server that restarts mid-ingest loses the job but not the work:
            # indexed files leave library-incoming as they finish, so re-running
            # the ingest resumes. Say so rather than waiting on a job that is gone.
            print(f"\nLost contact with the ingest: {exc}", file=sys.stderr)
            print(
                "Files already indexed are out of the queue, so re-running with --ingest\n"
                "resumes from where it stopped. For a batch that keeps crashing the\n"
                "server, scripts/robust_library_ingest.py drives it to the end.",
                file=sys.stderr,
            )
            return 1

        done, total = job.get("files_done") or 0, job.get("files_total") or 0
        line = f"  {job.get('phase') or job.get('status')}: {done}/{total} — {job.get('filename') or ''}".rstrip()
        if line != last:
            print(line)
            last = line

        if job.get("status") not in {"running", "queued", "pending"}:
            print(f"\n{job.get('message', '')}")
            report = job.get("library_report") or {}
            for failure in report.get("failed_files") or []:
                print(f"  FAILED {failure.get('filename')}: {failure.get('error')}")
            return 0 if job.get("status") == "done" else 1


def with_split_originals(names: set[str]) -> set[str]:
    """Add back the original filename of every PDF the server split into parts.

    A PDF too large to index whole is split into ``<stem>__p00001-00500.pdf``
    parts and the original deleted, so the original filename is absent from the
    index even though its contents are in it. Without this, the largest documents
    in the corpus — the ones that cost the most to send — would be uploaded and
    re-split on every run.
    """
    originals = set()
    for name in names:
        match = PART_NAME_RE.match(Path(name).stem)
        if match:
            originals.add(match.group("stem") + Path(name).suffix)
    return names | originals


def fetch_indexed_names(base_url: str, token: str) -> set[str]:
    """Filenames already in the search index."""
    return with_split_originals(set(api_json(base_url, token, "/files").get("files") or []))


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
    parser.add_argument(
        "--group", default="", choices=("", *LIBRARY_GROUPS),
        help="File every document in this folder on one Document Library index page. "
        "Without it, each document's page is inferred from its filename.",
    )
    parser.add_argument(
        "--ingest", action="store_true",
        help="Index the uploaded files once every batch is in, and follow the job "
        "to the end instead of leaving you to poll it",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-already-incoming", action="store_true",
        help="Query /admin/library/incoming first and skip filenames already queued "
        "(safe to resume an interrupted run without re-uploading/duplicating files)",
    )
    parser.add_argument(
        "--skip-already-indexed", action="store_true",
        help="Query /files first and send only the documents that are not in the "
        "database yet — for a folder where most of the contents are already there",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    excludes = set(args.exclude)
    if (args.skip_already_incoming or args.skip_already_indexed) and not (args.url and args.token):
        print(
            "--skip-already-incoming and --skip-already-indexed have to ask the server "
            "what is already there. Set --url/--token or SPK_URL/SPK_TOKEN.",
            file=sys.stderr,
        )
        return 1

    # Kept apart from --exclude: a corpus contributes thousands of names, and
    # printing them all as "excluded" would bury the handful the caller named.
    already_there: set[str] = set()
    if args.skip_already_incoming:
        already_there |= fetch_incoming_names(args.url, args.token)
    if args.skip_already_indexed:
        already_there |= fetch_indexed_names(args.url, args.token)

    found = discover_files(root, excludes)
    files = [f for f in found if f.name not in already_there]
    skipped = len(found) - len(files)

    print(f"Found {len(found)} ingestible file(s) in {root}")
    if excludes:
        print(f"Excluded {len(excludes)} by name: {sorted(excludes)}")
    if skipped:
        print(f"Skipped {skipped} already on the server; {len(files)} new.")
    if not files:
        # Having nothing left to send is the goal, not a failure — only an empty
        # folder means the caller probably pointed at the wrong one.
        if skipped:
            print("Nothing new to upload — every file in this folder is already there.")
            return 0
        print("No ingestible files found.", file=sys.stderr)
        return 1

    total_bytes = sum(f.stat().st_size for f in files)
    batches = batch_files(files, args.max_zip_mb * 1024 * 1024)

    print(f"Uploading {len(files)} file(s), {total_bytes / (1024**3):.2f} GB total")
    print(f"Split into {len(batches)} zip batch(es) (max {args.max_zip_mb} MB each)")
    if args.group:
        print(f"Index page: every file will be filed under '{args.group}'")
    else:
        print("Index page: inferred per file from its name")

    if args.dry_run:
        # The point of the dry run is to recognize the files, so name them rather
        # than count them — up to the point where the list stops being readable.
        for f in files[:DRY_RUN_LIST_LIMIT]:
            print(f"  {f.relative_to(root)}")
        if len(files) > DRY_RUN_LIST_LIMIT:
            print(f"  ... and {len(files) - DRY_RUN_LIST_LIMIT} more")
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
        result = upload_zip(args.url, args.token, data, label, args.group)
        print(f"  -> {result.get('message')}")

    print("\nAll batches uploaded.")
    if args.group:
        print(
            "The group travels with the queued files, so the ingest files them under\n"
            f"'{args.group}' without any extra flag."
        )

    if args.ingest:
        return run_ingest(args.url, args.token)

    print("\nCheck the queue:")
    print(f"  curl -s {args.url}/admin/library/incoming -H \"Authorization: Bearer $SPK_TOKEN\"")
    print("Then start ingest (or re-run this with --ingest):")
    print(f"  curl -s -X POST {args.url}/admin/library/ingest -H \"Authorization: Bearer $SPK_TOKEN\"")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ApiError as exc:
        # A stack trace here says nothing the message does not, and buries it.
        print(f"\n{exc}", file=sys.stderr)
        raise SystemExit(1) from None
