#!/usr/bin/env python3
"""
Drive /admin/library/ingest to completion, auto-resuming if the server
process crashes/restarts mid-job (e.g. under memory pressure from a large
batch). Ingest is file-level resumable: files that finish are removed from
library-incoming immediately, so simply re-triggering the endpoint picks up
where a crashed run left off. This script automates that retry loop.

Usage:
    export SPK_URL="https://projectspk-production.up.railway.app"
    export SPK_TOKEN="$(cat /tmp/spk_token.txt)"
    export SPK_LOGIN_EMAIL="william.a.pratt@usace.army.mil"   # for auto re-login
    python3 -u scripts/robust_library_ingest.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

SPK_URL = os.environ.get("SPK_URL", "").rstrip("/")
SPK_TOKEN = os.environ.get("SPK_TOKEN", "")
LOGIN_EMAIL = os.environ.get("SPK_LOGIN_EMAIL", "")

POLL_SECONDS = 20
HEALTH_RETRY_SECONDS = 15
HEALTH_MAX_WAIT_SECONDS = 300
MAX_CRASH_RETRIES = 30
REQUEST_TIMEOUT = 30


def _request(method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
    url = f"{SPK_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_health() -> bool:
    deadline = time.time() + HEALTH_MAX_WAIT_SECONDS
    while time.time() < deadline:
        try:
            result = _request("GET", "/health")
            if result.get("status") == "ok":
                return True
        except Exception:
            pass
        time.sleep(HEALTH_RETRY_SECONDS)
    return False


def relogin() -> str:
    if not LOGIN_EMAIL:
        raise RuntimeError(
            "Token invalid/expired and SPK_LOGIN_EMAIL is not set — cannot auto re-login."
        )
    result = _request("POST", "/login", body={"email": LOGIN_EMAIL})
    return result["token"]


def incoming_count(token: str) -> int:
    result = _request("GET", "/admin/library/incoming", token=token)
    return int(result.get("incoming_count", 0))


def start_ingest(token: str) -> str:
    result = _request("POST", "/admin/library/ingest", token=token)
    return result["job_id"]


def poll_job(token: str, job_id: str) -> dict:
    return _request("GET", f"/jobs/{job_id}", token=token)


def main() -> int:
    if not SPK_URL:
        print("SPK_URL is not set.", file=sys.stderr)
        return 1

    token = SPK_TOKEN
    crash_retries = 0

    while True:
        try:
            remaining = incoming_count(token)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                print("Token invalid/expired — re-logging in...")
                token = relogin()
                continue
            raise

        print(f"[{time.strftime('%H:%M:%S')}] library-incoming remaining: {remaining}")
        if remaining == 0:
            print("Done — library-incoming is empty. All files ingested.")
            return 0

        try:
            job_id = start_ingest(token)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                token = relogin()
                continue
            print(f"Failed to start ingest job: {exc}", file=sys.stderr)
            time.sleep(HEALTH_RETRY_SECONDS)
            continue

        print(f"  started job {job_id}")
        job_crashed = False

        while True:
            time.sleep(POLL_SECONDS)
            try:
                status = poll_job(token, job_id)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    token = relogin()
                    continue
                job_crashed = True
                print(f"  job poll failed ({exc}) — assuming server crash/restart")
                break
            except Exception as exc:
                job_crashed = True
                print(f"  job poll failed ({exc}) — assuming server crash/restart")
                break

            if "job_id" not in status:
                # Not a real job-status payload — e.g. a gateway error page
                # (like {"status":"error","code":502,...}) served with a
                # 200 status. Treat as a crash, not a job-level error.
                job_crashed = True
                print(f"  unexpected response (likely a crashed/restarting server): {status}")
                break

            state = status.get("status")
            if state in ("queued", "running"):
                print(f"  {status.get('message', '')}")
                continue
            if state == "done":
                print(f"  job finished: {status.get('message', status)}")
                break
            if state == "error":
                # Job-level error (not a crash) — server responded, so don't
                # treat this as a crash retry; just log it and move on to
                # re-check the queue (whatever succeeded before the error
                # is already out of library-incoming).
                print(f"  job reported error: {status}")
                break
            # Any unrecognized terminal state — stop polling, log, move on.
            print(f"  job status: {status}")
            break

        if job_crashed:
            crash_retries += 1
            if crash_retries > MAX_CRASH_RETRIES:
                print(f"Exceeded {MAX_CRASH_RETRIES} crash retries — giving up.", file=sys.stderr)
                return 1
            print(f"  waiting for server to come back (crash retry {crash_retries}/{MAX_CRASH_RETRIES})...")
            if not wait_for_health():
                print("  server did not come back within timeout — giving up.", file=sys.stderr)
                return 1
            print("  server is back up — resuming.")

        # Loop back around: re-check incoming count, start a fresh job if needed.


if __name__ == "__main__":
    raise SystemExit(main())
