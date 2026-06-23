"""Official publication dates from publications.usace.army.mil."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.doc_metadata import _library_title_case

DATES_FILE = (
    Path(__file__).resolve().parents[1] / "static" / "usace_publication_dates.json"
)

_SERIES_RE = re.compile(
    r"^(AR|ER|EM|EP|EC|ECB|ETL|PN|OM)\s*([\d][\d\-]*)$",
    re.IGNORECASE,
)


def normalize_doc_number(doc_number: str | None) -> str:
    if not doc_number:
        return ""
    text = re.sub(r"\s+", " ", doc_number.upper().strip())
    compact = text.replace(" ", "")
    for prefix in ("ER", "EM", "EP", "EC", "ECB", "ETL", "AR", "PN", "OM"):
        if compact.startswith(prefix):
            rest = compact[len(prefix) :].lstrip("-")
            if rest:
                return f"{prefix} {rest}"
    match = _SERIES_RE.match(text.replace(" ", " "))
    if match:
        return f"{match.group(1).upper()} {match.group(2)}"
    return text


def _year_from_date(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"(\d{4})", value)
    return match.group(1) if match else ""


def date_label_and_year(pub_date: str, latest_review: str = "") -> tuple[str, str]:
    pub_year = _year_from_date(pub_date)
    review_year = _year_from_date(latest_review)
    if review_year and pub_year and int(review_year) > int(pub_year):
        return "updated", review_year
    return "published", pub_year or review_year or "—"


def load_official_publications(path: Path | None = None) -> dict[str, dict[str, str]]:
    file_path = path or DATES_FILE
    if not file_path.exists():
        return {}

    data = json.loads(file_path.read_text(encoding="utf-8"))
    lookup: dict[str, dict[str, str]] = {}
    for item in data.get("publications") or []:
        key = normalize_doc_number(item.get("pub_number"))
        if key:
            lookup[key] = item
    return lookup


def official_fields(
    doc_number: str | None,
    lookup: dict[str, dict[str, str]] | None = None,
) -> dict[str, str] | None:
    table = lookup if lookup is not None else load_official_publications()
    key = normalize_doc_number(doc_number)
    if not key or key not in table:
        return None

    row = table[key]
    label, year = date_label_and_year(
        row.get("pub_date") or "",
        row.get("latest_review") or "",
    )
    title = _library_title_case((row.get("title") or "").strip())
    return {
        "display_title": title,
        "date_label": label,
        "date_year": year,
        "year_published": _year_from_date(row.get("pub_date") or ""),
        "year_updated": _year_from_date(row.get("latest_review") or ""),
        "official_url": (
            "https://www.publications.usace.army.mil/USACE-Publications/"
            f"{row.get('category', '').replace(' ', '-')}/"
        ),
    }
