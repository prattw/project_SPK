#!/usr/bin/env python3
"""Pull the Friday 5pm Pacific weekly usage report from Project SPK.

Usage:
  export SPK_URL="https://projectspk-production.up.railway.app"
  # Prefer durable APP_API_KEY (Railway). Login session tokens expire in 24h.
  export SPK_TOKEN="your-app-api-key"

  # Latest completed week (previous Friday 5pm → this Friday 5pm PT)
  python3 scripts/weekly_usage_report.py

  # Persist a snapshot on the server data volume
  python3 scripts/weekly_usage_report.py --save

  # Specific week ending date (Friday)
  python3 scripts/weekly_usage_report.py --week-ending 2026-07-18

  # JSON instead of text
  python3 scripts/weekly_usage_report.py --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


def fetch(url: str, token: str) -> tuple[int, str]:
    if shutil.which("curl"):
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
                "Accept: application/json, text/plain",
                url,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "curl failed").strip())
        body, _, status_part = proc.stdout.rpartition("\n__HTTP_STATUS__:")
        return int(status_part or "0"), body.strip()

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("SPK_URL", ""))
    parser.add_argument("--token", default=os.environ.get("SPK_TOKEN", ""))
    parser.add_argument("--week-ending", default=None, help="YYYY-MM-DD (Friday)")
    parser.add_argument("--save", action="store_true", help="Persist snapshot on server")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument("--out", default="", help="Optional file path to write the report")
    args = parser.parse_args()

    if not args.url or not args.token:
        print("Set SPK_URL and SPK_TOKEN", file=sys.stderr)
        return 1

    params: dict[str, str] = {}
    if args.week_ending:
        params["week_ending"] = args.week_ending
    if args.save:
        params["save"] = "true"
    query = urllib.parse.urlencode(params)
    path = "/usage/weekly" if args.json else "/usage/weekly/text"
    url = f"{args.url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"

    status, body = fetch(url, args.token)
    if status >= 400:
        print(f"HTTP {status}: {body}", file=sys.stderr)
        return 1

    if args.out:
        Path = __import__("pathlib").Path
        Path(args.out).write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)

    if args.json:
        try:
            print(json.dumps(json.loads(body), indent=2))
        except json.JSONDecodeError:
            print(body)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
