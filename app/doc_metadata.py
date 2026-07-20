"""Infer structured metadata from USACE / federal acquisition document filenames.

USACE publications follow predictable numbering conventions
(ER 1110-345-721, EM 1110-2-2704, EP 1100-2-1, ECB 2026-12, CECW-2018-05, ...).
Parsing them lets retrieval filter and cite by document series without
needing to open the file.
"""

from __future__ import annotations

import re
from datetime import datetime
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
_PART_RE = re.compile(
    r"\b(?:part|pt\.?)\s*([IVXLC\d]+|[A-Z])\b",
    re.IGNORECASE,
)
_VOLUME_RE = re.compile(
    r"\b(?:vol(?:ume)?|book)\.?\s*([IVXLC\d]+|[A-Z])\b",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(
    r"\b(?:sec(?:tion)?)\.?\s*([IVXLC\d]+|[A-Z])\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _normalize_part_label(kind: str, value: str) -> str:
    return f"{kind} {value.strip().upper()}"


def extract_part(filename: str) -> str:
    """Return a normalized part/volume label when the filename indicates division."""
    stem = Path(filename).stem
    for pattern, kind in (
        (_PART_RE, "Part"),
        (_VOLUME_RE, "Volume"),
        (_SECTION_RE, "Section"),
    ):
        match = pattern.search(stem)
        if match:
            return _normalize_part_label(kind, match.group(1))
    return ""


# Project files uploaded before session tagging was added.
USER_UPLOAD_SOURCES = frozenset(
    {
        "DVA Liv Phase A_Volume 5A_Specs_Conformed Set_2024-08-30_.pdf",
        "DVA Liv Phase A_Volume 5B_Specs_Conformed Set_2024-08-30_.pdf",
        "Submittal 09 62 29-1 Cork Tile_PD_SD_1_.pdf",
    }
)


def extract_year_from_filename(filename: str) -> str:
    match = _YEAR_RE.search(Path(filename).stem)
    return match.group(0) if match else ""


def extract_year(filename: str, updated_at: str | None = None) -> str:
    if updated_at:
        try:
            iso = updated_at.replace("Z", "+00:00")
            return str(datetime.fromisoformat(iso).year)
        except ValueError:
            pass
    return extract_year_from_filename(filename)


def _pdf_meta_year(raw: object | None) -> str:
    if not raw:
        return ""
    match = re.search(r"(\d{4})", str(raw))
    return match.group(1) if match else ""


def pdf_date_years(path: Path) -> tuple[str, str]:
    """Return (published_year, modified_year) from PDF embedded metadata."""
    if path.suffix.lower() != ".pdf" or not path.exists():
        return "", ""
    try:
        from pypdf import PdfReader

        meta = PdfReader(str(path)).metadata
        if not meta:
            return "", ""
        published = _pdf_meta_year(getattr(meta, "creation_date", None) or meta.get("/CreationDate"))
        modified = _pdf_meta_year(getattr(meta, "modification_date", None) or meta.get("/ModDate"))
        return published, modified
    except Exception:
        return "", ""


_SMALL_WORDS = frozenset({"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "with"})


def _library_title_case(text: str) -> str:
    words = text.split()
    if not words:
        return text
    out: list[str] = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i > 0 and lower in _SMALL_WORDS:
            out.append(lower)
        elif word.isupper() and len(word) <= 5:
            out.append(word)
        else:
            out.append(word.capitalize())
    return " ".join(out)


def display_title(filename: str, doc_number: str = "") -> str:
    """Human-readable title with document number and part markers removed."""
    stem = Path(filename).stem
    title = _clean_title(stem)

    if doc_number:
        num_pattern = re.escape(doc_number).replace(r"\ ", r"[\s_-]+")
        title = re.sub(rf"^{num_pattern}[\s_.-]*", "", title, flags=re.IGNORECASE)

    for pattern in (_PART_RE, _VOLUME_RE, _SECTION_RE):
        title = pattern.sub("", title)
    title = _YEAR_RE.sub("", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" -.,_")
    if not title:
        title = _clean_title(stem)
    return _library_title_case(title)


def library_date_fields(
    source: str,
    entry: dict[str, str],
    file_path: Path | None = None,
    *,
    read_pdf: bool = False,
) -> dict[str, str]:
    """Choose published vs updated year for Document Library display."""
    published = entry.get("year_published") or extract_year_from_filename(source)
    updated = entry.get("year_updated") or ""

    if read_pdf and file_path and file_path.exists():
        pub_pdf, mod_pdf = pdf_date_years(file_path)
        if pub_pdf and not published:
            published = pub_pdf
        if mod_pdf:
            updated = mod_pdf

    if not updated:
        updated = extract_year(source, entry.get("updated_at"))

    has_revision = bool(
        published and updated and published.isdigit() and updated.isdigit() and int(updated) > int(published)
    )
    if has_revision:
        return {
            "year_published": published,
            "year_updated": updated,
            "date_label": "updated",
            "date_year": updated,
        }

    return {
        "year_published": published,
        "year_updated": updated,
        "date_label": "published",
        "date_year": published or updated,
    }


def enrich_library_fields(
    source: str,
    entry: dict[str, str],
    file_path: Path | None = None,
    *,
    read_pdf: bool = False,
) -> dict[str, str]:
    """Add display fields used by the Document Library tab."""
    doc_number = entry.get("doc_number") or ""
    part = entry.get("part") or extract_part(source)
    dates = library_date_fields(source, entry, file_path, read_pdf=read_pdf)
    return {
        "display_title": entry.get("display_title") or display_title(source, doc_number),
        "part": part,
        **dates,
    }


_corpus_index: dict[str, Path] | None = None


def _build_corpus_index() -> dict[str, Path]:
    """One-time filename → path map for data/ and DOCUMENTS for RAG."""
    global _corpus_index
    if _corpus_index is not None:
        return _corpus_index

    from app.config import settings

    index: dict[str, Path] = {}
    roots = [settings.data_path, settings.data_path.parent / "DOCUMENTS for RAG"]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith("."):
                continue
            index.setdefault(path.name, path)
            try:
                rel = str(path.relative_to(root))
                index.setdefault(rel, path)
            except ValueError:
                pass
    _corpus_index = index
    return index


def resolve_document_path(source: str) -> Path | None:
    """Locate an indexed file under data/ or the DOCUMENTS for RAG corpus."""
    index = _build_corpus_index()
    if source in index:
        return index[source]
    return index.get(Path(source).name)


def classify_upload_origin(source: str, entry: dict[str, str]) -> str:
    """User Uploads: known project files or files uploaded in an active session."""
    if source in USER_UPLOAD_SOURCES:
        return "user"
    if entry.get("session_id"):
        return "user"
    return "library"


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
    part = extract_part(filename)
    if part:
        meta["part"] = part
    meta["display_title"] = display_title(filename, doc_number)
    year = extract_year(filename)
    if year:
        meta["year_updated"] = year
    return meta


def category_folder(category: str) -> str:
    return CATEGORY_FOLDERS.get(category, "misc")
