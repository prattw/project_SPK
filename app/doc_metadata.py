"""Infer structured metadata from USACE / federal acquisition document filenames.

USACE publications follow predictable numbering conventions
(ER 1110-345-721, EM 1110-2-2704, EP 1100-2-1, ECB 2026-12, CECW-2018-05, ...).
Parsing them lets retrieval filter and cite by document series without
needing to open the file.
"""

from __future__ import annotations

import re
from pathlib import Path

SERIES_NAMES = {
    "ER": "Engineer Regulation",
    "EM": "Engineer Manual",
    "EP": "Engineer Pamphlet",
    "EC": "Engineer Circular",
    "ECB": "Engineering & Construction Bulletin",
    "ETL": "Engineer Technical Letter",
}

# Categories double as the proposed folder structure for the corpus.
CATEGORY_FOLDERS = {
    "engineer-regulation": "regulations/ER",
    "engineer-manual": "manuals/EM",
    "engineer-pamphlet": "pamphlets/EP",
    "engineer-circular": "circulars/EC",
    "engineer-technical-letter": "technical-letters/ETL",
    "ecb": "bulletins/ECB",
    "ufc": "criteria/UFC",
    "tspwg": "criteria/TSPWG",
    "mil-std": "criteria/MIL-STD",
    "om": "memos/OM",
    "pn": "notices/PN",
    "tm": "manuals/TM",
    "space-planning": "criteria/space-planning",
    "tri-service-wg": "criteria/tri-service-working-groups",
    "us-code": "statutes",
    "hq-memo": "policy-memos",
    "command-guidance": "policy-memos/command-guidance",
    "acquisition-regulation": "acquisition",
    "udg-uai": "udg-uai",
    "idac": "udg-uai/idac",
    "course-material": "course-materials",
    "misc": "misc",
}

_SERIES_RE = re.compile(
    r"(?<![A-Za-z])(ER|EM|EP|EC)[ _-]{0,2}(\d{1,4}(?:-\d+){1,4}|\d{2,4})",
    re.IGNORECASE,
)
_ETL_RE = re.compile(
    r"(?<![A-Za-z])ETL[ _-]{0,2}(\d{1,4}(?:[-_]\d+){0,4})", re.IGNORECASE
)
_ECB_RE = re.compile(r"\becb[ _-]?(\d{4})[ _-](\d{1,3})(?!\d)", re.IGNORECASE)
_OM_RE = re.compile(r"^OM[ _-]{1,2}([\d-]+)", re.IGNORECASE)
_PN_RE = re.compile(r"^PN[ _-]{1,2}([\d-]+)", re.IGNORECASE)
_HQ_MEMO_RE = re.compile(
    r"^(CECW|CECI|CEMP|CEEO|CERM|CGM|CIO)[-_ (]", re.IGNORECASE
)


def _clean_title(stem: str) -> str:
    title = re.sub(r"[_]+", " ", stem)
    title = re.sub(r"\s{2,}", " ", title)
    title = re.sub(r"\s*\(\d+\)\s*$", "", title)  # trailing "(1)" copy markers
    return title.strip(" -.")


def infer_doc_metadata(filename: str) -> dict[str, str]:
    """Return chunk-safe metadata (string values only) inferred from a filename."""
    stem = Path(filename).stem
    upper = stem.upper()

    doc_type = ""
    doc_number = ""
    category = "misc"

    ecb = _ECB_RE.search(stem)
    etl = _ETL_RE.search(stem)
    series = _SERIES_RE.search(stem)
    om = _OM_RE.match(stem)
    pn = _PN_RE.match(stem)

    if ecb:
        doc_type = SERIES_NAMES["ECB"]
        doc_number = f"ECB {ecb.group(1)}-{ecb.group(2)}"
        category = "ecb"
    elif etl:
        doc_type = SERIES_NAMES["ETL"]
        doc_number = f"ETL {etl.group(1).replace('_', '-')}"
        category = "engineer-technical-letter"
    elif upper.startswith("UFC") or re.search(r"\bUFC\b", upper):
        doc_type = "Unified Facilities Criteria"
        doc_number = "UFC"
        category = "ufc"
    elif upper.startswith("TSPWG"):
        doc_type = "Tri-Service Pavements Working Group Manual"
        category = "tspwg"
    elif re.match(r"^T[A-Z]{1,6}EWG", upper):
        doc_type = "Tri-Service Engineering Working Group Document"
        category = "tri-service-wg"
    elif re.match(r"^TM[ _-]?5", upper):
        tm = re.match(r"^TM[ _-]?(5(?:[-_]\d+)*)", upper)
        doc_type = "Army Technical Manual"
        doc_number = "TM " + tm.group(1).replace("_", "-")
        category = "tm"
    elif "SPACEPLA" in upper.replace(" ", "") or upper.startswith("SPACE PLA"):
        doc_type = "Space Planning Criteria"
        category = "space-planning"
    elif re.match(r"^USC\d+", upper):
        doc_type = "United States Code"
        doc_number = stem.upper()[:20]
        category = "us-code"
    elif re.search(r"MIL[-_ ]?STD[-_ ]?(\d+)", upper):
        doc_type = "Military Standard"
        doc_number = "MIL-STD-" + re.search(r"MIL[-_ ]?STD[-_ ]?(\d+)", upper).group(1)
        category = "mil-std"
    elif om:
        doc_type = "USACE Operations Memorandum"
        doc_number = f"OM {om.group(1)}"
        category = "om"
    elif pn:
        doc_type = "USACE Publication Notice"
        doc_number = f"PN {pn.group(1)}"
        category = "pn"
    elif series:
        code = series.group(1).upper()
        doc_type = SERIES_NAMES[code]
        doc_number = f"{code} {series.group(2)}"
        category = {
            "ER": "engineer-regulation",
            "EM": "engineer-manual",
            "EP": "engineer-pamphlet",
            "EC": "engineer-circular",
        }[code]
    elif _HQ_MEMO_RE.match(stem):
        doc_type = "USACE HQ Policy Memo"
        doc_number = stem.split(".")[0][:40]
        category = "hq-memo"
    elif upper.startswith("CG, USACE"):
        doc_type = "Command Guidance Memo"
        category = "command-guidance"
    elif "DFARS" in upper:
        doc_type = "Defense FAR Supplement"
        doc_number = "DFARS"
        category = "acquisition-regulation"
    elif "AFARS" in upper:
        doc_type = "Army FAR Supplement"
        doc_number = "AFARS"
        category = "acquisition-regulation"
    elif re.search(r"\bPGI\b|^pgi[-_]", stem, re.IGNORECASE):
        doc_type = "DFARS Procedures, Guidance & Information"
        doc_number = "PGI"
        category = "acquisition-regulation"
    elif re.search(r"\bFAR\b", upper):
        doc_type = "Federal Acquisition Regulation"
        doc_number = "FAR"
        category = "acquisition-regulation"
    elif "IDAC" in upper.replace("-", "") or "IDaC".upper() in upper:
        doc_type = "Integrated Design & Construction (IDaC)"
        category = "idac"
    elif "UDG" in upper or "UAI" in upper:
        doc_type = "USACE Acquisition Instruction / Desk Guide"
        category = "udg-uai"
    elif re.match(r"^SEC\d{2}", upper):
        doc_type = "F&A Course Material"
        category = "course-material"

    meta: dict[str, str] = {"title": _clean_title(stem), "category": category}
    if doc_type:
        meta["doc_type"] = doc_type
    if doc_number:
        meta["doc_number"] = doc_number
    return meta


def category_folder(category: str) -> str:
    return CATEGORY_FOLDERS.get(category, "misc")
