"""Read a ProjNet DrChecks XML export into comment records.

ProjNet's export element names vary by report. This walks the file and treats
a repeating element as a comment when it carries an id and some review text.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import Any

COMMENT_TAGS = {
    "comment",
    "reviewcomment",
    "drcheck",
    "drcheckscomment",
    "item",
}

_ID_KEYS = (
    "commentid",
    "comment_id",
    "id",
    "itemid",
    "commentnumber",
    "comment_number",
)
_FIELD_ALIASES = {
    "discipline": ("discipline", "disc"),
    "document": ("document", "sheet", "drawing", "dwg", "location"),
    "section": ("section", "specsection", "spec_section", "spec"),
    "comment": ("comment", "commenttext", "comment_text", "text", "description", "issue"),
    "response": ("response", "evaluation", "evaluatorresponse", "responsetext", "response_text"),
    "backcheck": ("backcheck", "back_check", "backchecktext", "backcheck_text"),
    "status": ("status", "state", "commentstatus"),
    "criticality": ("critical", "criticality", "priority", "type"),
    "reviewer": ("reviewer", "commenter", "author", "submittedby"),
    "evaluator": ("evaluator", "designer", "responder"),
}


def _local(tag: str) -> str:
    return tag.split("}")[-1].strip().lower().replace(" ", "").replace("-", "_")


def _text(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _children(element: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for child in list(element):
        key = _local(child.tag)
        value = _text(child)
        if not key or not value:
            continue
        if key in fields:
            fields[key] = f"{fields[key]}\n{value}"
        else:
            fields[key] = value
    for attr, value in element.attrib.items():
        key = _local(attr)
        if key and value.strip() and key not in fields:
            fields[key] = value.strip()
    return fields


def _contains_comment(element: ET.Element) -> bool:
    for child in element.iter():
        if child is element:
            continue
        if _local(child.tag) in COMMENT_TAGS:
            return True
    return False


def _repeating_records(root: ET.Element) -> list[ET.Element]:
    groups: dict[str, list[ET.Element]] = {}
    for parent in root.iter():
        names = [_local(child.tag) for child in list(parent)]
        if len(names) < 2:
            continue
        if len(set(names)) == 1:
            groups.setdefault(names[0], []).extend(list(parent))
    if not groups:
        return []
    return max(groups.values(), key=len)


def _pick(raw: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if raw.get(name):
            return raw[name]
    return ""


def _record(element: ET.Element, source_file: str, index: int) -> dict[str, Any]:
    raw = _children(element)
    comment = _pick(raw, _FIELD_ALIASES["comment"])
    comment_id = _pick(raw, _ID_KEYS)
    if not comment_id:
        digest = hashlib.sha1(f"{source_file}:{index}:{comment}".encode()).hexdigest()[:10]
        comment_id = f"import-{digest}"
    record = {
        "id": comment_id,
        "discipline": _pick(raw, _FIELD_ALIASES["discipline"]),
        "document": _pick(raw, _FIELD_ALIASES["document"]),
        "section": _pick(raw, _FIELD_ALIASES["section"]),
        "comment": comment,
        "response": _pick(raw, _FIELD_ALIASES["response"]),
        "backcheck": _pick(raw, _FIELD_ALIASES["backcheck"]),
        "status": _pick(raw, _FIELD_ALIASES["status"]),
        "criticality": _pick(raw, _FIELD_ALIASES["criticality"]),
        "reviewer": _pick(raw, _FIELD_ALIASES["reviewer"]),
        "evaluator": _pick(raw, _FIELD_ALIASES["evaluator"]),
        "source_file": source_file,
        "raw": raw,
    }
    return record


def parse_review_xml(data: bytes, source_file: str = "upload.xml") -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Not a ProjNet XML export: {exc}") from exc

    nodes = [
        element
        for element in root.iter()
        if _local(element.tag) in COMMENT_TAGS and not _contains_comment(element)
    ]
    if not nodes:
        nodes = _repeating_records(root)

    records = []
    for index, element in enumerate(nodes, start=1):
        record = _record(element, source_file, index)
        if record["comment"] or record["response"] or record["backcheck"]:
            records.append(record)
    if not records:
        raise ValueError("No comments found in that XML. Export the full DrChecks report.")
    return records
