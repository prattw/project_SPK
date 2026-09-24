"""Group the Document Library into the three index pages users browse.

Every indexed document already carries a fine-grained ``category`` from
:mod:`app.doc_metadata` (``engineer-regulation``, ``acquisition-regulation``,
``army-regulation``, ...). This module rolls those ~24 categories up into the
three top-level indexes:

* ``engineering``           — Government Engineering documents
* ``contracting-law``       — Government Contracting & Law documents
* ``discipline-knowledge``  — Discipline Knowledge documents

Army Regulations and DA Pamphlets span both engineering and legal/administrative
subject matter, so they are routed by their series number (AR 420-1 is facilities
engineering; AR 27-1 is legal services) instead of by category alone.

Inference only works on documents whose filename follows a publication naming
convention. A folder of discipline references — textbooks, handbooks, course
decks, district guidance — has no such convention, so those documents can instead
be *assigned* a group when they are uploaded. The assignment is stored on every
chunk under :data:`GROUP_META_KEY` and is authoritative, which is what makes
"everything in this folder belongs on this page" a deterministic statement rather
than a guess about filenames.

The default mapping can be overridden at runtime — without a redeploy — by
placing a ``library_groups.json`` file in the data directory. See
``load_group_overrides`` for the accepted shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.config import settings

ENGINEERING = "engineering"
CONTRACTING_LAW = "contracting-law"
DISCIPLINE_KNOWLEDGE = "discipline-knowledge"

DEFAULT_GROUP = DISCIPLINE_KNOWLEDGE

OVERRIDE_FILENAME = "library_groups.json"

# Chunk metadata key holding an explicit, upload-time group assignment.
GROUP_META_KEY = "library_group"

# Display metadata for the three index pages, in tab order.
GROUP_ORDER: tuple[str, ...] = (ENGINEERING, CONTRACTING_LAW, DISCIPLINE_KNOWLEDGE)

GROUP_LABELS: dict[str, str] = {
    ENGINEERING: "Government Engineering",
    CONTRACTING_LAW: "Government Contracting & Law",
    DISCIPLINE_KNOWLEDGE: "Discipline Knowledge",
}

GROUP_DESCRIPTIONS: dict[str, str] = {
    ENGINEERING: (
        "Engineer Regulations, Manuals, Pamphlets and Circulars, ECBs, ETLs, "
        "Unified Facilities Criteria, Tri-Service criteria, and USACE engineering policy memoranda."
    ),
    CONTRACTING_LAW: (
        "FAR, DFARS, AFARS, PGI, United States Code, USACE acquisition instructions and desk guides, "
        "and Army regulations governing legal, contracting, and information management."
    ),
    DISCIPLINE_KNOWLEDGE: (
        "Training material, course slides, discipline references, and supporting documents "
        "that are not numbered USACE publications or acquisition regulations."
    ),
}

# Fine-grained category -> index page.
CATEGORY_GROUPS: dict[str, str] = {
    # --- Government Engineering ---
    "engineer-regulation": ENGINEERING,
    "engineer-manual": ENGINEERING,
    "engineer-pamphlet": ENGINEERING,
    "engineer-circular": ENGINEERING,
    "engineer-technical-letter": ENGINEERING,
    "ecb": ENGINEERING,
    "ufc": ENGINEERING,
    "tspwg": ENGINEERING,
    "tri-service-wg": ENGINEERING,
    "mil-std": ENGINEERING,
    "tm": ENGINEERING,
    "space-planning": ENGINEERING,
    "om": ENGINEERING,
    "pn": ENGINEERING,
    "hq-memo": ENGINEERING,
    "command-guidance": ENGINEERING,
    # --- Government Contracting & Law ---
    "acquisition-regulation": CONTRACTING_LAW,
    "us-code": CONTRACTING_LAW,
    "udg-uai": CONTRACTING_LAW,
    "idac": CONTRACTING_LAW,
    # --- Discipline Knowledge ---
    "course-material": DISCIPLINE_KNOWLEDGE,
    "misc": DISCIPLINE_KNOWLEDGE,
}

# AR / DA PAM series that are engineering subject matter. Everything else in the
# AR/PAM families is treated as legal, contracting, or administrative policy.
AR_PAM_ENGINEERING_SERIES: frozenset[str] = frozenset(
    {
        "200",  # Environmental protection and enhancement
        "210",  # Installations
        "350",  # Training (range and facility support)
        "385",  # Safety
        "405",  # Real estate
        "415",  # Construction
        "420",  # Facilities management
        "525",  # Military operations / protective design
    }
)

AR_PAM_CONTRACTING_LAW_SERIES: frozenset[str] = frozenset(
    {
        "11",  # Army programs / management control
        "15",  # Boards, commissions, committees
        "25",  # Information management and records
        "27",  # Legal services
        "36",  # Audit
        "37",  # Financial administration
        "70",  # Research, development, acquisition
        "700",  # Logistics
        "715",  # Procurement
    }
)

AR_PAM_CATEGORIES: frozenset[str] = frozenset({"army-regulation", "da-pamphlet"})

_SERIES_PREFIX_RE = re.compile(r"(\d{1,4})")

_overrides_cache: dict[str, Any] | None = None


def group_override_path() -> Path:
    return settings.data_path / OVERRIDE_FILENAME


def load_group_overrides(*, refresh: bool = False) -> dict[str, Any]:
    """Load optional runtime overrides from ``{DATA_DIR}/library_groups.json``.

    Accepted shape (every key optional)::

        {
          "categories": {"course-material": "engineering"},
          "doc_number_prefixes": {"AR 420": "engineering"},
          "sources": {"Some Exact Filename.pdf": "discipline-knowledge"}
        }

    Unknown group names are ignored so a typo cannot blank out a page.
    """
    global _overrides_cache
    if _overrides_cache is not None and not refresh:
        return _overrides_cache

    overrides: dict[str, Any] = {"categories": {}, "doc_number_prefixes": {}, "sources": {}}
    path = group_override_path()
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key in overrides:
                section = raw.get(key)
                if not isinstance(section, dict):
                    continue
                overrides[key] = {
                    str(name): str(group)
                    for name, group in section.items()
                    if str(group) in GROUP_LABELS
                }
    except Exception as exc:  # noqa: BLE001 — bad override file must not break the library
        print(f"Library group overrides ignored ({path}): {exc}")

    _overrides_cache = overrides
    return overrides


def _series_prefix(doc_number: str | None) -> str | None:
    """Return the leading series number of an AR/PAM doc number ("AR 420-1" -> "420")."""
    if not doc_number:
        return None
    match = _SERIES_PREFIX_RE.search(doc_number)
    return match.group(1) if match else None


def _ar_pam_group(doc_number: str | None) -> str:
    series = _series_prefix(doc_number)
    if series and series in AR_PAM_ENGINEERING_SERIES:
        return ENGINEERING
    if series and series in AR_PAM_CONTRACTING_LAW_SERIES:
        return CONTRACTING_LAW
    return CONTRACTING_LAW


def valid_group(value: Any) -> str | None:
    """Canonical group key for a stored or user-supplied value, else ``None``.

    Chroma hands back whatever was written, so a stale or hand-edited metadata
    value must never be trusted enough to route a document to a page that does
    not exist.
    """
    if not isinstance(value, str):
        return None
    return normalize_group(value)


def library_group(
    category: str | None,
    doc_number: str | None = None,
    source: str | None = None,
    assigned: str | None = None,
) -> str:
    """Return the index page a document belongs to.

    Precedence, most specific first: an exact source override (the admin naming
    one file), the group assigned to the document at upload time, a doc-number
    prefix override, a category override, AR/PAM series routing, the built-in
    category map, then the default page.

    An assigned group outranks every form of inference because someone stated it
    on purpose; it yields only to an exact-filename override, which is the
    narrower statement of the two.
    """
    overrides = load_group_overrides()

    if source:
        mapped = overrides["sources"].get(source)
        if mapped:
            return mapped

    explicit = valid_group(assigned)
    if explicit:
        return explicit

    if doc_number:
        upper = doc_number.upper()
        # Longest prefix wins so "AR 420-1" beats "AR 420".
        for prefix in sorted(overrides["doc_number_prefixes"], key=len, reverse=True):
            if upper.startswith(prefix.upper()):
                return overrides["doc_number_prefixes"][prefix]

    normalized = (category or "").strip().lower()

    mapped = overrides["categories"].get(normalized)
    if mapped:
        return mapped

    if normalized in AR_PAM_CATEGORIES:
        return _ar_pam_group(doc_number)

    return CATEGORY_GROUPS.get(normalized, DEFAULT_GROUP)


def group_label(group: str) -> str:
    return GROUP_LABELS.get(group, GROUP_LABELS[DEFAULT_GROUP])


def normalize_group(value: str | None) -> str | None:
    """Resolve a user-supplied group name (or label) to a canonical key."""
    if not value:
        return None
    candidate = value.strip().lower().replace("_", "-").replace(" ", "-")
    if candidate in GROUP_LABELS:
        return candidate
    aliases = {
        "engineering-documents": ENGINEERING,
        "government-engineering": ENGINEERING,
        "contracting": CONTRACTING_LAW,
        "contracting-and-law": CONTRACTING_LAW,
        "government-contracting-&-law": CONTRACTING_LAW,
        "law": CONTRACTING_LAW,
        "discipline": DISCIPLINE_KNOWLEDGE,
        "knowledge": DISCIPLINE_KNOWLEDGE,
    }
    return aliases.get(candidate)


def group_summary(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-page document and chunk counts, in tab order."""
    counts: dict[str, dict[str, int]] = {
        group: {"documents": 0, "chunks": 0} for group in GROUP_ORDER
    }
    for doc in documents:
        group = valid_group(doc.get("library_group")) or library_group(
            doc.get("category"), doc.get("doc_number"), doc.get("source")
        )
        bucket = counts.setdefault(group, {"documents": 0, "chunks": 0})
        bucket["documents"] += 1
        bucket["chunks"] += int(doc.get("chunks") or 0)

    return [
        {
            "group": group,
            "label": GROUP_LABELS[group],
            "description": GROUP_DESCRIPTIONS[group],
            "documents": counts.get(group, {}).get("documents", 0),
            "chunks": counts.get(group, {}).get("chunks", 0),
        }
        for group in GROUP_ORDER
    ]
