#!/usr/bin/env python3
"""Pull the Friday 5pm Pacific weekly usage report from Project SPK.

Usage:
  export SPK_URL="https://projectspk-production.up.railway.app"

  # Sign in with a usage-admin roster email (same as the web login)
  python3 scripts/weekly_usage_report.py --login-email william.a.pratt@usace.army.mil

  # Or use a durable APP_API_KEY / bearer token
  export SPK_TOKEN="your-app-api-key"
  python3 scripts/weekly_usage_report.py

  # Persist a snapshot on the server data volume
  python3 scripts/weekly_usage_report.py --login-email william.a.pratt@usace.army.mil --save

  # Specific week ending date (Friday)
  python3 scripts/weekly_usage_report.py --login-email william.a.pratt@usace.army.mil --week-ending 2026-07-18

  # JSON instead of text
  python3 scripts/weekly_usage_report.py --login-email william.a.pratt@usace.army.mil --json
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
from pathlib import Path


DEFAULT_URL = "https://projectspk-production.up.railway.app"


def _curl_json(method: str, url: str, *, token: str | None = None, data: str | None = None) -> tuple[int, str]:
    cmd = ["curl", "-sS", "--max-time", "60", "-w", "\n__HTTP_STATUS__:%{http_code}", "-X", method]
    if token:
        cmd.extend(["-H", f"Authorization: Bearer {token}"])
    if data is not None:
        cmd.extend(["-H", "Content-Type: application/json", "--data-binary", data])
    cmd.extend(["-H", "Accept: application/json, text/plain", url])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "curl failed").strip())
    body, _, status_part = proc.stdout.rpartition("\n__HTTP_STATUS__:")
    return int(status_part or "0"), body.strip()


def fetch(url: str, token: str) -> tuple[int, str]:
    if shutil.which("curl"):
        return _curl_json("GET", url, token=token)

    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/plain"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def login(url: str, email: str) -> str:
    """Obtain a short-lived bearer token via POST /login (roster email)."""
    payload = json.dumps({"email": email.strip().lower()})
    login_url = f"{url.rstrip('/')}/login"
    if shutil.which("curl"):
        status, body = _curl_json("POST", login_url, data=payload)
    else:
        req = urllib.request.Request(
            login_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, body = resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read().decode("utf-8", errors="replace")

    if status >= 400:
        raise RuntimeError(f"Login failed HTTP {status}: {body}")
    try:
        token = json.loads(body).get("token") or ""
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Login returned non-JSON: {body[:200]}") from exc
    if not token:
        raise RuntimeError(f"Login response missing token: {body[:200]}")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("SPK_URL", DEFAULT_URL),
        help=f"App base URL (default: {DEFAULT_URL} or SPK_URL)",
    )
    parser.add_argument("--token", default=os.environ.get("SPK_TOKEN", ""), help="Bearer token or APP_API_KEY")
    parser.add_argument(
        "--login-email",
        default=os.environ.get("SPK_LOGIN_EMAIL", ""),
        help="Roster email to POST /login for a short-lived token",
    )
    parser.add_argument("--week-ending", default=None, help="YYYY-MM-DD (Friday)")
    parser.add_argument("--save", action="store_true", help="Persist snapshot on server")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    parser.add_argument("--out", default="", help="Optional file path to write the report")
    args = parser.parse_args()

    if not args.url:
        print("Set SPK_URL or pass --url", file=sys.stderr)
        return 1

    token = args.token
    if not token and args.login_email:
        try:
            token = login(args.url, args.login_email)
        except Exception as exc:  # noqa: BLE001 — CLI surface
            print(str(exc), file=sys.stderr)
            return 1
    if not token:
        print("Pass --login-email, or set SPK_TOKEN / --token", file=sys.stderr)
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

    status, body = fetch(url, token)
    if status >= 400:
        print(f"HTTP {status}: {body}", file=sys.stderr)
        return 1

    if args.out:
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
