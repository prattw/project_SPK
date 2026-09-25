"""Check publication numbers cited in a comment against the local USACE list.

The list is the publications.usace.army.mil scrape already kept for Project SPK.
It does not include UFC or UFGS currency. Those have to be confirmed on WBDG.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATES_FILE = Path(__file__).resolve().parents[1] / "static" / "usace_publication_dates.json"

_CITATION_RE = re.compile(
    r"\b(UFC|UFGS|ER|EM|EP|EC|ECB|ETL|AR)\s*([0-9][0-9.\-]*)",
    re.IGNORECASE,
)
_USACE_SERIES = {"ER", "EM", "EP", "EC", "ECB", "ETL", "AR"}


def normalize_doc_number(series: str, number: str) -> str:
    return f"{series.upper()} {number.strip()}"


@lru_cache(maxsize=1)
def load_publications(path: str = "") -> tuple[dict[str, dict[str, str]], str]:
    file_path = Path(path) if path else DATES_FILE
    if not file_path.exists():
        return {}, ""
    data = json.loads(file_path.read_text(encoding="utf-8"))
    lookup: dict[str, dict[str, str]] = {}
    for item in data.get("publications") or []:
        key = re.sub(r"\s+", " ", (item.get("pub_number") or "").upper()).strip()
        if key:
            lookup[key] = item
    return lookup, str(data.get("scraped") or "")


def citations_in(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _CITATION_RE.finditer(text or ""):
        series = match.group(1).upper()
        number = match.group(2).strip(".")
        key = f"{series} {number}"
        if key not in seen:
            seen.add(key)
            found.append((series, number))
    return found


def check_text(
    text: str,
    lookup: dict[str, dict[str, str]] | None = None,
    scraped: str = "",
) -> list[dict[str, str]]:
    if lookup is None:
        lookup, scraped = load_publications()
    table = lookup
    results: list[dict[str, str]] = []
    for series, number in citations_in(text):
        key = normalize_doc_number(series, number)
        if series in {"UFC", "UFGS"}:
            results.append(
                {
                    "citation": key,
                    "status": "confirm-wbdg",
                    "detail": (
                        f"{key} is not in the USACE publications list. "
                        "Confirm whether it is current on WBDG before the comment treats it as active or inactive."
                    ),
                }
            )
            continue
        if series not in _USACE_SERIES:
            continue
        row = table.get(key)
        if row is None:
            results.append(
                {
                    "citation": key,
                    "status": "not-in-list",
                    "detail": (
                        f"{key} is not in the local USACE publications list"
                        + (f" (scraped {scraped})" if scraped else "")
                        + ". Do not state that it is current."
                    ),
                }
            )
            continue
        published = row.get("pub_date") or "unknown date"
        title = (row.get("title") or "").strip()
        results.append(
            {
                "citation": key,
                "status": "listed",
                "detail": f"{key} is on the USACE list as “{title}”, published {published}. The list does not say it is inactive.",
            }
        )
    return results
