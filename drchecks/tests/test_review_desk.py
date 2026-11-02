"""Review Desk tests that do not need Ollama."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drafts import LocalModelRequired, assert_local_model, draft_prompt  # noqa: E402
from publications import check_text  # noqa: E402
from xml_import import parse_review_xml  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


sample = (ROOT / "samples" / "sample_review.xml").read_bytes()
records = parse_review_xml(sample, "sample_review.xml")
check("sample has two comments", len(records) == 2, str(len(records)))
check("comment id kept", records[0]["id"] == "1001")
check("spec section mapped", records[0]["section"] == "33 40 00")
check("evaluation text mapped", "Concur" in records[1]["response"])

facts = check_text(records[0]["comment"])
listed = [item for item in facts if item["citation"] == "ER 1110-1-1807"]
check("known ER is on the USACE list", bool(listed) and listed[0]["status"] == "listed")

ufc = check_text(records[1]["comment"])
ufc_row = [item for item in ufc if item["citation"].startswith("UFC")]
check("UFC is sent to WBDG, not declared inactive", bool(ufc_row) and ufc_row[0]["status"] == "confirm-wbdg")

check("localhost ollama is allowed", assert_local_model("http://127.0.0.1:11434/v1").startswith("http://127.0.0.1"))
try:
    assert_local_model("https://api.openai.com/v1")
    check("OpenAI URL is refused", False)
except LocalModelRequired:
    check("OpenAI URL is refused", True)

prompt = draft_prompt(records[1], "backcheck", "The response names no edition.", ufc)
check("backcheck prompt includes the response", "next issue of the drawings" in prompt)
check("prompt does not invent inactivity", "inactive" not in prompt.lower() or "unless the note" in prompt.lower())

print(f"\n{PASSED} passed, {len(FAILED)} failed")
raise SystemExit(1 if FAILED else 0)
