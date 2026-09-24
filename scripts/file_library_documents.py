#!/usr/bin/env python3
"""Move already-indexed documents onto a Document Library index page.

Filing is the only way onto the Discipline Knowledge page, so this is how titles
join it after a bulk upload has parked everything on Miscellaneous. Nothing is
re-embedded — only the chunk metadata is rewritten — so refiling a large corpus is
cheap.

Reports what it would move and changes nothing until you pass --apply, because
--matching is a substring search and a loose pattern can sweep up far more than
you meant.

Usage:
  export SPK_URL="https://YOUR-APP.up.railway.app"
  export SPK_TOKEN="paste-token-here"

  # See what these patterns catch
  python3 scripts/file_library_documents.py --group discipline-knowledge \
    --matching "Advances in" --matching "Cost Estimation" \
    --matching "Designing Data" --matching "Algorithmic Trading"

  # File them
  python3 scripts/file_library_documents.py --group discipline-knowledge \
    --matching "Advances in" --matching "Cost Estimation" \
    --matching "Designing Data" --matching "Algorithmic Trading" --apply

  # Exact filenames instead of patterns
  python3 scripts/file_library_documents.py --group engineering \
    --source "ARN15118_AR 420-1_FINAL.pdf" --apply

  # Everything on one page that nobody filed there
  python3 scripts/file_library_documents.py --group engineering \
    --from-group miscellaneous --inferred-only --apply

On Windows use `python` rather than `python3`, and set the variables with
`$env:SPK_URL = "..."`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# Mirrors app/library_groups.py GROUP_ORDER.
LIBRARY_GROUPS = ("engineering", "contracting-law", "discipline-knowledge", "miscellaneous")

REQUEST_TIMEOUT_SECONDS = 300


def api_post(base_url: str, token: str, path: str, body: dict) -> tuple[int, dict | str]:
    """POST JSON via curl, matching the rest of the admin tooling (avoids macOS
    Python SSL cert issues)."""
    proc = subprocess.run(
        [
            "curl", "-sS", "--max-time", str(REQUEST_TIMEOUT_SECONDS),
            "-w", "\n__HTTP_STATUS__:%{http_code}",
            "-X", "POST",
            f"{base_url.rstrip('/')}{path}",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-d", json.dumps(body),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "curl failed")
    payload, _, status = proc.stdout.rpartition("\n__HTTP_STATUS__:")
    try:
        return int(status or "0"), json.loads(payload)
    except json.JSONDecodeError:
        return int(status or "0"), payload.strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--group", required=True, choices=LIBRARY_GROUPS, help="Page to file them on")
    parser.add_argument(
        "--matching", action="append", default=[], metavar="TEXT",
        help="Substring of a filename (repeatable)",
    )
    parser.add_argument(
        "--source", action="append", default=[], metavar="FILENAME",
        help="Exact indexed filename (repeatable)",
    )
    parser.add_argument(
        "--from-group", default="", choices=("", *LIBRARY_GROUPS),
        help="Take everything currently on this page",
    )
    parser.add_argument(
        "--inferred-only", action="store_true",
        help="With --from-group, spare the documents filed on that page on purpose",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually move them. Without this, nothing is written.",
    )
    parser.add_argument("--url", default=os.environ.get("SPK_URL", ""))
    parser.add_argument("--token", default=os.environ.get("SPK_TOKEN", ""))
    args = parser.parse_args()

    if not (args.matching or args.source or args.from_group):
        print("Nothing selected: pass --matching, --source, or --from-group.", file=sys.stderr)
        return 2
    if not args.url or not args.token:
        print("Set --url/--token or SPK_URL/SPK_TOKEN env vars.", file=sys.stderr)
        return 2

    body: dict = {"group": args.group, "dry_run": not args.apply}
    if args.matching:
        body["patterns"] = args.matching
    if args.source:
        body["sources"] = args.source
    if args.from_group:
        body["from_group"] = args.from_group
        body["inferred_only"] = args.inferred_only

    code, result = api_post(args.url, args.token, "/admin/library/regroup", body)
    if code >= 400:
        detail = result.get("detail") if isinstance(result, dict) else result
        print(f"HTTP {code}: {detail}", file=sys.stderr)
        return 1
    if not isinstance(result, dict):
        print(f"Unexpected response: {result}", file=sys.stderr)
        return 1

    moved = result.get("would_move") or result.get("updated") or []
    missing = result.get("not_found") or []

    if args.apply:
        print(f"Filed {len(moved)} document(s) on {result.get('label', args.group)}.")
    else:
        print(f"Would file {len(moved)} document(s) on {args.group}. Nothing written.")
    for name in moved:
        print(f"  {name}")
    for name in missing:
        print(f"  (not indexed) {name}")

    if not args.apply and moved:
        print("\nIf that list is right, run the same command again with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
