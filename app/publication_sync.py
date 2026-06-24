"""Check USACE / federal publication sites for newly listed documents."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from app.config import settings
from app.doc_metadata import infer_doc_metadata
from app.usace_publication_sites import USACE_PUBLICATION_CATEGORY_SITES

# Sites listed in README (USACE publications roadmap) plus each USACE Publications
# category listing page (checked for newly listed documents each session).
USACE_SYNC_SITES = [
    {
        "name": "USACE Library",
        "url": "https://www.usace.army.mil/Resources/Library/",
    },
    {
        "name": "USACE Library Program",
        "url": "https://www.usace.army.mil/Resources/Library/Library-Program/",
    },
    {
        "name": "USACE Publications",
        "url": "https://www.publications.usace.army.mil/",
    },
    {
        "name": "ERDC Library",
        "url": "https://www.erdc.usace.army.mil/Library.aspx",
    },
    {
        "name": "USACE Geospatial Open Data",
        "url": "https://geospatial-usace.opendata.arcgis.com/",
    },
    *USACE_PUBLICATION_CATEGORY_SITES,
]

_PUB_HINT = re.compile(
    r"(?:ER|EM|EP|EC|ECB|ETL|UFC|UFGS|AR|PAM|FAR|DFARS|AFARS|PGI)\s*[\d\-]+",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
_SYNC_STATE = settings.data_path / ".publication_sync.json"
_FETCH_TIMEOUT = 20
_USER_AGENT = "ProjectSPK/0.7 (+https://github.com; USACE publication sync)"


def _sync_state_path() -> Path:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    return _SYNC_STATE


def load_sync_state() -> dict:
    path = _sync_state_path()
    if not path.exists():
        return {"last_sync": None, "known_urls": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_sync": None, "known_urls": []}


def save_sync_state(state: dict) -> None:
    path = _sync_state_path()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": _USER_AGENT})
    with urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
        return resp.read(500_000).decode("utf-8", errors="replace")


def _extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()

    for match in _HREF_RE.finditer(html):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)

        # Pull nearby anchor text (rough window after the href).
        tail = html[match.end() : match.end() + 200]
        text_match = re.search(r">([^<]{4,120})<", tail)
        title = (text_match.group(1) if text_match else "").strip()
        if not title:
            title = Path(urlparse(absolute).path).name or absolute

        pub_match = _PUB_HINT.search(title) or _PUB_HINT.search(absolute)
        doc_number = pub_match.group(0).upper().replace("_", " ") if pub_match else ""

        if not doc_number and not absolute.lower().endswith(".pdf"):
            # Keep only publication-like links on listing pages.
            if "publication" not in absolute.lower() and "library" not in absolute.lower():
                continue

        meta = infer_doc_metadata(title or absolute)
        found.append(
            {
                "title": title[:200],
                "url": absolute,
                "doc_number": meta.get("doc_number") or doc_number,
                "doc_type": meta.get("doc_type", ""),
                "site": base_url,
            }
        )

    return found


def check_publication_sites(force: bool = False) -> dict:
    """
    Fetch USACE publication listing pages and report links not seen before.
    Results are cached for 6 hours unless force=True.
    """
    state = load_sync_state()
    last = state.get("last_sync")
    if not force and last:
        try:
            last_dt = datetime.fromisoformat(last)
            age_hours = (datetime.now(tz=timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours < 6:
                return {
                    "status": "cached",
                    "last_sync": last,
                    "new_publications": state.get("last_new", []),
                    "sites_checked": len(USACE_SYNC_SITES),
                    "message": f"Using cached sync from {last_dt.strftime('%Y-%m-%d %H:%M UTC')} "
                    f"({age_hours:.1f}h ago).",
                }
        except ValueError:
            pass

    known: set[str] = set(state.get("known_urls") or [])
    indexed = set(get_rag_sources())
    new_items: list[dict] = []
    errors: list[str] = []
    all_found: list[dict] = []

    for site in USACE_SYNC_SITES:
        try:
            html = _fetch_html(site["url"])
            title_match = _TITLE_RE.search(html)
            page_title = title_match.group(1).strip() if title_match else site["name"]
            links = _extract_links(html, site["url"])
            for link in links:
                link["site_name"] = site["name"]
                link["page_title"] = page_title
                all_found.append(link)
                url = link["url"]
                if url not in known:
                    known.add(url)
                    # Flag as "new" if not already indexed by doc number or filename.
                    doc_num = link.get("doc_number") or ""
                    filename_hint = Path(urlparse(url).path).name
                    already_indexed = (
                        any(doc_num and doc_num in src for src in indexed)
                        or filename_hint in indexed
                    )
                    if not already_indexed:
                        new_items.append(link)
        except Exception as exc:
            errors.append(f"{site['name']}: {exc}")

    now = datetime.now(tz=timezone.utc).isoformat()
    state.update(
        {
            "last_sync": now,
            "known_urls": sorted(known),
            "last_new": new_items[:50],
        }
    )
    save_sync_state(state)

    return {
        "status": "ok",
        "last_sync": now,
        "sites_checked": len(USACE_SYNC_SITES),
        "links_found": len(all_found),
        "new_publications": new_items[:50],
        "errors": errors,
        "message": (
            f"Checked {len(USACE_SYNC_SITES)} USACE sites — "
            f"{len(new_items)} newly listed publication link(s) not yet indexed."
            if not errors
            else f"Checked sites with {len(errors)} error(s); {len(new_items)} new link(s) found."
        ),
    }


def get_rag_sources() -> list[str]:
    from app.rag import get_rag

    return get_rag().list_sources()
