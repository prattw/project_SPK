#!/usr/bin/env python3
"""Live terminal progress for a library ingest job on Project SPK.

Uses only the Python standard library plus curl (built into macOS).

On macOS, Python's built-in HTTPS client often fails certificate verification
unless you run "Install Certificates.command". This script uses curl instead,
which matches the rest of the Project SPK admin workflow.

Usage:
  export SPK_URL="https://projectspk-production.up.railway.app"
  export SPK_TOKEN="your-bearer-token"
  python3 scripts/watch_library_ingest.py 5743d9db-df04-46c3-860b-b56a00119b3c

Or:
  ./scripts/watch_library_ingest.sh JOB_ID
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class FetchError(Exception):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def fetch_job_curl(base_url: str, token: str, job_id: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/jobs/{job_id}"
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "60",
            "-w",
            "\n__HTTP_STATUS__:%{http_code}",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Accept: application/json",
            url,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "curl request failed").strip()
        raise FetchError(detail)

    body, _, status_part = proc.stdout.rpartition("\n__HTTP_STATUS__:")
    status = int(status_part or "0")
    body = body.strip()
    if status >= 400:
        raise FetchError(f"HTTP {status}: {body}", status=status)
    return json.loads(body)


def fetch_job_urllib(base_url: str, token: str, job_id: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/jobs/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise FetchError(f"HTTP {exc.code}: {body}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise FetchError(str(exc.reason)) from exc


def fetch_job(base_url: str, token: str, job_id: str) -> dict[str, Any]:
    if shutil.which("curl"):
        return fetch_job_curl(base_url, token, job_id)
    return fetch_job_urllib(base_url, token, job_id)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def eta(elapsed_s: float, done: int, total: int) -> str:
    if total <= 0 or done <= 0 or elapsed_s <= 0:
        return "—"
    remaining = (elapsed_s / done) * (total - done)
    return f"~{format_duration(remaining)} left"


def progress_bar(done: int, total: int, width: int = 36) -> str:
    if total <= 0:
        return "░" * width
    ratio = min(1.0, max(0.0, done / total))
    filled = int(width * ratio)
    return "█" * filled + "░" * (width - filled)


def phase_label(job: dict[str, Any]) -> str:
    phase = (job.get("phase") or "").strip()
    if phase:
        return phase.upper()
    message = (job.get("message") or "").lower()
    if "split" in message:
        return "SPLIT"
    if "index" in message:
        return "INGEST"
    if job.get("status") == "done":
        return "DONE"
    return "PREPARE"


def truncate(text: str, max_len: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def render(job: dict[str, Any], *, started_at: float, poll_n: int) -> str:
    if job.get("detail") and not job.get("job_id"):
        return "\n".join(
            [
                "",
                "  Project SPK — Library ingest",
                "  " + "─" * 52,
                "  ✗ Could not load job",
                f"    {job['detail']}",
                "",
                "  If the server restarted (e.g. after a deploy), job progress is",
                "  cleared from memory. The ingest may have stopped — check Railway",
                "  logs, then restart with POST /admin/library/ingest if needed.",
                "",
            ]
        )

    status = job.get("status") or "unknown"
    total = int(job.get("files_total") or 0)
    done = int(job.get("files_done") or 0)
    pct = (100.0 * done / total) if total else 0.0
    elapsed_s = time.time() - started_at
    elapsed_ms = job.get("elapsed_ms")
    if isinstance(elapsed_ms, int) and elapsed_ms > 0:
        elapsed_s = elapsed_ms / 1000.0

    phase = phase_label(job)
    detail = job.get("filename") or job.get("message") or ""
    chunks = int(job.get("chunks_indexed") or 0)
    message = job.get("message") or ""

    lines = [
        "",
        "  Project SPK — Library ingest",
        "  " + "─" * 52,
        f"  Job      {job.get('job_id', '?')}",
        f"  Status   {status.upper()}   Phase: {phase}",
        "",
    ]

    if total > 0:
        lines.extend(
            [
                f"  Files    {done:,} / {total:,}  ({pct:5.1f}%)",
                f"  [{progress_bar(done, total)}]",
                f"  Elapsed  {format_duration(elapsed_s)}    ETA  {eta(elapsed_s, done, total)}",
            ]
        )
    else:
        lines.extend(
            [
                f"  Progress {message or 'Starting…'}",
                f"  Elapsed  {format_duration(elapsed_s)}",
            ]
        )

    if detail:
        lines.append(f"  Current  {truncate(detail, 70)}")

    if chunks:
        lines.append(f"  Chunks   {chunks:,} indexed so far")

    lines.append(f"  Poll     #{poll_n}  (Ctrl+C to stop watching — job keeps running)")
    lines.append("")

    if status == "done":
        report = job.get("library_report") or {}
        lines.extend(
            [
                "  ✓ Complete",
                f"    Indexed: {report.get('files_indexed', done):,} files",
                f"    Chunks:  {report.get('chunks_indexed', chunks):,}",
                f"    Failed:  {report.get('files_failed', 0):,}",
                f"    Split:   {report.get('split_pdfs', 0):,} large PDF(s)",
            ]
        )
        purged = report.get("purged_sources")
        if purged:
            lines.append(f"    Purged:  {purged:,} old chunk(s)")
        lines.append("")

    if status == "error":
        lines.extend(["  ✗ Error", f"    {message}", ""])

    return "\n".join(lines)


def clear_and_print(text: str) -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch a Project SPK library ingest job.")
    parser.add_argument("job_id", help="Job ID returned by POST /admin/library/ingest")
    parser.add_argument(
        "--interval",
        type=float,
        default=10.0,
        help="Seconds between polls (default: 10)",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("SPK_URL", ""),
        help="App base URL (or set SPK_URL)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("SPK_TOKEN", ""),
        help="Bearer token (or set SPK_TOKEN)",
    )
    args = parser.parse_args()

    if not args.url:
        print("Set SPK_URL or pass --url", file=sys.stderr)
        return 1
    if not args.token:
        print("Set SPK_TOKEN or pass --token", file=sys.stderr)
        return 1

    started_at = time.time()
    poll_n = 0
    terminal_status = {"done", "error"}

    try:
        while True:
            poll_n += 1
            try:
                job = fetch_job(args.url, args.token, args.job_id)
            except FetchError as exc:
                if exc.status:
                    clear_and_print(f"\n  HTTP {exc.status}: {exc}\n")
                    return 1
                clear_and_print(
                    f"\n  Network error: {exc}\n  Retrying in {args.interval:.0f}s…\n"
                )
                time.sleep(args.interval)
                continue
            except json.JSONDecodeError as exc:
                clear_and_print(f"\n  Invalid JSON from server: {exc}\n")
                return 1

            clear_and_print(render(job, started_at=started_at, poll_n=poll_n))
            if job.get("status") in terminal_status:
                return 0 if job.get("status") == "done" else 1

            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        sys.stdout.write("\nStopped watching (ingest continues on the server).\n")
        sys.stdout.flush()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
