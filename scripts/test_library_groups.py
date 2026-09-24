#!/usr/bin/env python3
"""Tests for filing library documents onto a specific index page.

Covers group resolution precedence, the library-incoming group manifest (including
split-PDF parts), ingest-time tagging, retagging already-indexed documents, and the
admin API surface. Embeddings are stubbed, so no OpenAI key is required.

    python3 scripts/test_library_groups.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="spk-groups-test-"))
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["CHROMA_PERSIST_DIR"] = str(_TMP / "chroma")
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["ACCESS_ROSTER"] = "admin@usace.army.mil,someone.else@usace.army.mil"
os.environ["USAGE_ADMIN_EMAILS"] = "admin@usace.army.mil"
os.environ["APP_API_KEY"] = ""

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


def section(title: str) -> None:
    print(f"\n{title}")


# --- Stub embeddings before anything imports them for real. ------------------
import app.embeddings as embeddings  # noqa: E402

embeddings.embed_texts = lambda texts: [[0.0] * 8 for _ in texts]
embeddings.embed_query = lambda text: [0.0] * 8

import app.rag as rag_module  # noqa: E402

rag_module.embed_texts = embeddings.embed_texts
rag_module.embed_query = embeddings.embed_query

from app.config import settings  # noqa: E402
from app.library_groups import (  # noqa: E402
    CONTRACTING_LAW,
    DISCIPLINE_KNOWLEDGE,
    ENGINEERING,
    GROUP_META_KEY,
    group_summary,
    library_group,
    load_group_overrides,
    normalize_group,
    valid_group,
)
from app.library_ingest import (  # noqa: E402
    forget_incoming_groups,
    group_manifest_path,
    library_incoming_path,
    read_incoming_groups,
    record_incoming_group,
    resolve_group,
    run_library_ingest,
    save_incoming_upload,
)
from app.rag import get_rag  # noqa: E402


def reset_manifest() -> None:
    group_manifest_path().unlink(missing_ok=True)


def clear_incoming() -> None:
    incoming = library_incoming_path()
    for path in incoming.iterdir():
        if path.is_file():
            path.unlink()


def text_file(name: str, body: str = "Placeholder content for testing.") -> bytes:
    return body.encode()


# ---------------------------------------------------------------------------
section("Group names")

check("canonical key accepted", normalize_group("discipline-knowledge") == DISCIPLINE_KNOWLEDGE)
check("label form accepted", normalize_group("Discipline Knowledge") == DISCIPLINE_KNOWLEDGE)
check("alias accepted", normalize_group("discipline") == DISCIPLINE_KNOWLEDGE)
check("underscores accepted", normalize_group("contracting_law") == CONTRACTING_LAW)
check("nonsense rejected", normalize_group("not-a-page") is None)
check("empty rejected", normalize_group("") is None)
check("valid_group ignores non-strings", valid_group(17) is None)
check("valid_group ignores None", valid_group(None) is None)
check("valid_group canonicalizes", valid_group("Engineering") == ENGINEERING)


# ---------------------------------------------------------------------------
section("An assigned group outranks filename inference")

# A UFC filename infers engineering on its own.
check(
    "inference alone says engineering",
    library_group("ufc", "UFC 3-301-01", "UFC 3-301-01.pdf") == ENGINEERING,
)
check(
    "assignment overrides inference",
    library_group("ufc", "UFC 3-301-01", "UFC 3-301-01.pdf", assigned=DISCIPLINE_KNOWLEDGE)
    == DISCIPLINE_KNOWLEDGE,
)
check(
    "assignment survives an AR series rule",
    library_group("army-regulation", "AR 27-1", "AR 27-1.pdf", assigned=ENGINEERING) == ENGINEERING,
)
check(
    "garbage assignment falls back to inference",
    library_group("ufc", "UFC 3-301-01", "UFC 3-301-01.pdf", assigned="bogus") == ENGINEERING,
)
check(
    "no assignment behaves exactly as before",
    library_group("acquisition-regulation", "FAR", "FAR.pdf") == CONTRACTING_LAW,
)
check(
    "unrecognized document still defaults",
    library_group(None, None, "Some Handbook.pdf") == DISCIPLINE_KNOWLEDGE,
)

# An exact-filename override is narrower than a batch assignment, so it wins.
settings.data_path.mkdir(parents=True, exist_ok=True)
(settings.data_path / "library_groups.json").write_text(
    json.dumps({"sources": {"Pinned.pdf": CONTRACTING_LAW}}), encoding="utf-8"
)
load_group_overrides(refresh=True)
check(
    "exact-source override beats an assignment",
    library_group("ufc", "UFC 1-200-01", "Pinned.pdf", assigned=ENGINEERING) == CONTRACTING_LAW,
)
check(
    "override does not leak to other files",
    library_group("ufc", "UFC 1-200-01", "Other.pdf", assigned=ENGINEERING) == ENGINEERING,
)
(settings.data_path / "library_groups.json").unlink()
load_group_overrides(refresh=True)


# ---------------------------------------------------------------------------
section("The library-incoming group manifest")

reset_manifest()
check("manifest starts empty", read_incoming_groups() == {})
check("no manifest file when empty", not group_manifest_path().exists())

record_incoming_group("Handbook.pdf", "discipline-knowledge")
check("entry recorded", read_incoming_groups() == {"Handbook.pdf": DISCIPLINE_KNOWLEDGE})
check("manifest file written", group_manifest_path().is_file())
check("manifest is hidden", group_manifest_path().name.startswith("."))
check("manifest is not ingestable", group_manifest_path().suffix == ".json")

check("invalid group not recorded", record_incoming_group("X.pdf", "nope") is None)
check("invalid group left no entry", "X.pdf" not in read_incoming_groups())
check("None group not recorded", record_incoming_group("Y.pdf", None) is None)

record_incoming_group("Other.pdf", "Engineering")
check(
    "second entry coexists and canonicalizes",
    read_incoming_groups() == {"Handbook.pdf": DISCIPLINE_KNOWLEDGE, "Other.pdf": ENGINEERING},
)

forget_incoming_groups(["Handbook.pdf"])
check("entry forgotten", read_incoming_groups() == {"Other.pdf": ENGINEERING})
forget_incoming_groups(["Other.pdf"])
check("manifest removed when last entry goes", not group_manifest_path().exists())

group_manifest_path().write_text("{not json", encoding="utf-8")
check("corrupt manifest ignored, not fatal", read_incoming_groups() == {})
group_manifest_path().write_text(json.dumps(["a", "b"]), encoding="utf-8")
check("wrong-shaped manifest ignored", read_incoming_groups() == {})
reset_manifest()


# ---------------------------------------------------------------------------
section("Resolving a staged file to a page")

groups = {"Handbook.pdf": DISCIPLINE_KNOWLEDGE}
check("named file resolves", resolve_group("Handbook.pdf", groups, None) == DISCIPLINE_KNOWLEDGE)
check("batch default applies to others", resolve_group("Other.pdf", groups, ENGINEERING) == ENGINEERING)
check("per-file beats batch default", resolve_group("Handbook.pdf", groups, ENGINEERING) == DISCIPLINE_KNOWLEDGE)
check("no manifest and no default is untagged", resolve_group("Other.pdf", {}, None) is None)
check("invalid default is untagged", resolve_group("Other.pdf", {}, "nope") is None)

# Oversized PDFs are indexed as parts whose names the uploader never chose.
check(
    "split part inherits its parent's page",
    resolve_group("Handbook__p00001-00500.pdf", groups, None) == DISCIPLINE_KNOWLEDGE,
)
check(
    "second part inherits too",
    resolve_group("Handbook__p00501-01000.pdf", groups, None) == DISCIPLINE_KNOWLEDGE,
)
check(
    "part of an unassigned doc still takes the batch default",
    resolve_group("Stranger__p00001-00500.pdf", groups, ENGINEERING) == ENGINEERING,
)
check(
    "a name that merely contains __p is not treated as a part",
    resolve_group("__pals.pdf", groups, None) is None,
)


# ---------------------------------------------------------------------------
section("Ingest writes the assignment onto every chunk")

reset_manifest()
clear_incoming()

# A filename that would otherwise infer engineering, filed as discipline material.
save_incoming_upload(text_file("x"), "UFC 3-301-01 Course Extract.txt", "discipline-knowledge")
save_incoming_upload(text_file("x"), "Steel Design Handbook.txt", "discipline-knowledge")
save_incoming_upload(text_file("x"), "ER 1110-2-1150.txt", None)

check("manifest has the two assigned files", len(read_incoming_groups()) == 2)

report = run_library_ingest(library_incoming_path())
check("all three indexed", report.files_indexed == 3, f"got {report.files_indexed}")
check(
    "two filed under discipline knowledge",
    report.grouped_files.get(DISCIPLINE_KNOWLEDGE) == 2,
    str(report.grouped_files),
)
check("untagged file not counted as filed", sum(report.grouped_files.values()) == 2)
check("manifest cleaned up after ingest", not group_manifest_path().exists())

rag = get_rag()
docs = {d["source"]: d for d in rag.list_documents()}
check("assigned doc lands on discipline page",
      docs["UFC 3-301-01 Course Extract.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)
check("assignment stored on the chunks",
      docs["UFC 3-301-01 Course Extract.txt"]["assigned_group"] == DISCIPLINE_KNOWLEDGE)
check("handbook lands on discipline page",
      docs["Steel Design Handbook.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)
check("untagged ER still infers engineering",
      docs["ER 1110-2-1150.txt"]["library_group"] == ENGINEERING)
check("untagged doc has no assignment", not docs["ER 1110-2-1150.txt"].get("assigned_group"))

summary = {row["group"]: row["documents"] for row in group_summary(rag.list_documents())}
check("page counts reflect the assignment",
      summary[DISCIPLINE_KNOWLEDGE] == 2 and summary[ENGINEERING] == 1, str(summary))


# ---------------------------------------------------------------------------
section("A split document's parts follow it, and its manifest entry clears")

clear_incoming()
reset_manifest()

# Stand in for the output of split_oversized_pdfs: the oversized original is gone
# and only its page-range parts remain, under names nobody uploaded.
record_incoming_group("Big Design Guide.txt", "discipline-knowledge")
(library_incoming_path() / "Big Design Guide__p00001-00500.txt").write_bytes(text_file("a"))
(library_incoming_path() / "Big Design Guide__p00501-01000.txt").write_bytes(text_file("b"))

report = run_library_ingest(library_incoming_path())
check("both parts indexed", report.files_indexed == 2, f"got {report.files_indexed}")
check("both parts filed with the parent", report.grouped_files.get(DISCIPLINE_KNOWLEDGE) == 2, str(report.grouped_files))

docs = {d["source"]: d for d in get_rag().list_documents()}
check("first part on the discipline page",
      docs["Big Design Guide__p00001-00500.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)
check("second part on the discipline page",
      docs["Big Design Guide__p00501-01000.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)
check("the vanished parent leaves no stale entry", read_incoming_groups() == {}, str(read_incoming_groups()))


# ---------------------------------------------------------------------------
section("A batch default files untagged files")

clear_incoming()
save_incoming_upload(text_file("x"), "District Lessons Learned.txt", None)
report = run_library_ingest(library_incoming_path(), group="discipline-knowledge")
check("batch default applied", report.grouped_files.get(DISCIPLINE_KNOWLEDGE) == 1, str(report.grouped_files))

docs = {d["source"]: d for d in get_rag().list_documents()}
check("file landed on the requested page",
      docs["District Lessons Learned.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)


# ---------------------------------------------------------------------------
section("Retagging documents already in the index")

rag = get_rag()
result = rag.assign_library_group(["ER 1110-2-1150.txt"], "discipline-knowledge")
check("one source moved", result["updated"] == ["ER 1110-2-1150.txt"], str(result))
check("chunks were rewritten", result["chunks_updated"] > 0)
check("nothing reported missing", result["not_found"] == [])

docs = {d["source"]: d for d in rag.list_documents()}
check("moved document now on discipline page",
      docs["ER 1110-2-1150.txt"]["library_group"] == DISCIPLINE_KNOWLEDGE)
check("move survives the documents cache",
      docs["ER 1110-2-1150.txt"]["assigned_group"] == DISCIPLINE_KNOWLEDGE)

result = rag.assign_library_group(["Does Not Exist.pdf"], "engineering")
check("unknown source reported, not invented", result["not_found"] == ["Does Not Exist.pdf"])
check("unknown source moved nothing", result["updated"] == [])

try:
    rag.assign_library_group(["ER 1110-2-1150.txt"], "not-a-page")
    check("invalid group refused", False, "no exception raised")
except ValueError as exc:
    check("invalid group refused", "not-a-page" in str(exc))

matches = rag.sources_matching(["handbook"])
check("pattern match is case-insensitive", matches == ["Steel Design Handbook.txt"], str(matches))
check("pattern with no match is empty", rag.sources_matching(["zzzz"]) == [])
check("blank patterns match nothing", rag.sources_matching(["", "  "]) == [])

# Move it back so later assertions start from a known state.
rag.assign_library_group(["ER 1110-2-1150.txt"], "engineering")


# ---------------------------------------------------------------------------
section("Admin API")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.usage import init_usage_db  # noqa: E402

init_usage_db()
client = TestClient(app)
login = client.post("/login", json={"email": "admin@usace.army.mil"})
check("admin signed in", login.status_code == 200, login.text)
token = login.json().get("token", "")
auth = {"Authorization": f"Bearer {token}"}

clear_incoming()
reset_manifest()

resp = client.post(
    "/admin/library/upload?group=discipline-knowledge",
    headers=auth,
    files={"file": ("Concrete Handbook.txt", b"content", "text/plain")},
)
check("upload with a group accepted", resp.status_code == 200, resp.text)
check("response echoes the group", resp.json().get("group") == DISCIPLINE_KNOWLEDGE, resp.text)
check("message names the index page", "Discipline Knowledge" in resp.json().get("message", ""), resp.text)

resp = client.post(
    "/admin/library/upload?group=not-a-page",
    headers=auth,
    files={"file": ("Whatever.txt", b"content", "text/plain")},
)
check("unknown group rejected", resp.status_code == 400, resp.text)
detail = resp.json().get("detail", {})
check("rejection lists the valid pages",
      isinstance(detail, dict) and DISCIPLINE_KNOWLEDGE in detail.get("valid_groups", []), resp.text)

resp = client.post(
    "/admin/library/upload",
    headers=auth,
    files={"file": ("ER 1110-1-8162.txt", b"content", "text/plain")},
)
check("upload without a group still works", resp.status_code == 200, resp.text)
check("no group echoed", resp.json().get("group") is None, resp.text)

resp = client.get("/admin/library/incoming", headers=auth)
check("incoming listing ok", resp.status_code == 200, resp.text)
listing = resp.json()
by_name = {f["filename"]: f for f in listing["files"]}
check("queued file shows its page", by_name["Concrete Handbook.txt"]["group"] == DISCIPLINE_KNOWLEDGE)
check("untagged file shows none", by_name["ER 1110-1-8162.txt"]["group"] is None)
check("manifest not listed as a document", ".groups.json" not in by_name)
check("counts split by page", listing["by_group"] == {DISCIPLINE_KNOWLEDGE: 1, "unassigned": 1}, str(listing["by_group"]))

# A zip files its whole contents on one page.
buf = BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("Survey Methods.txt", "content")
    zf.writestr("UFC 4-010-01 Extract.txt", "content")
resp = client.post(
    "/admin/library/upload-zip?group=discipline-knowledge",
    headers=auth,
    files={"file": ("batch.zip", buf.getvalue(), "application/zip")},
)
check("zip upload with a group accepted", resp.status_code == 200, resp.text)
check("zip response echoes the group", resp.json().get("group") == DISCIPLINE_KNOWLEDGE, resp.text)
manifest = read_incoming_groups()
check("every zip member tagged",
      manifest.get("Survey Methods.txt") == DISCIPLINE_KNOWLEDGE
      and manifest.get("UFC 4-010-01 Extract.txt") == DISCIPLINE_KNOWLEDGE, str(manifest))
check("prior untagged file untouched", "ER 1110-1-8162.txt" not in manifest)

resp = client.post("/admin/library/ingest", headers=auth, json={"group": "not-a-page"})
check("ingest rejects an unknown group", resp.status_code == 400, resp.text)

resp = client.post("/admin/library/ingest", headers=auth, json={})
check("ingest starts", resp.status_code == 200, resp.text)
job_id = resp.json()["job_id"]

import time  # noqa: E402

for _ in range(100):
    status = client.get(f"/jobs/{job_id}", headers=auth).json()
    if status["status"] in {"done", "error"}:
        break
    time.sleep(0.1)
check("ingest finished", status["status"] == "done", status.get("message", ""))
grouped = (status.get("library_report") or {}).get("grouped_files", {})
check("three files filed on the discipline page", grouped.get(DISCIPLINE_KNOWLEDGE) == 3, str(grouped))
check("job message reports the filing", "Discipline Knowledge" in status.get("message", ""), status.get("message", ""))

resp = client.get("/library/groups", headers=auth)
check("groups endpoint ok", resp.status_code == 200, resp.text)
counts = {g["group"]: g["documents"] for g in resp.json()["groups"]}
check("UFC extract counted as discipline, not engineering", counts[DISCIPLINE_KNOWLEDGE] >= 6, str(counts))

resp = client.get(f"/library/groups/{DISCIPLINE_KNOWLEDGE}", headers=auth)
check("discipline page ok", resp.status_code == 200, resp.text)
page = resp.json()
sources = {d["source"] for d in page["documents"]}
check("uploaded handbook on the page", "Concrete Handbook.txt" in sources, str(sorted(sources)))
check("UFC extract on the page too", "UFC 4-010-01 Extract.txt" in sources, str(sorted(sources)))
check("page separates deliberate filing from inference",
      page["assigned_count"] + page["inferred_count"] == page["count"], str(page)[:200])
check("the documents we filed are counted as assigned", page["assigned_count"] >= 6, str(page["assigned_count"]))

# Engineering holds one document this test moved back by hand; the rest got there
# by filename inference, and the counts have to tell those apart.
eng_page = client.get(f"/library/groups/{ENGINEERING}", headers=auth).json()
check("engineering counts the one deliberate move", eng_page["assigned_count"] == 1, str(eng_page["assigned_count"]))
check("engineering still has inferred documents", eng_page["inferred_count"] >= 1, str(eng_page["inferred_count"]))

# Regroup an already-indexed document.
resp = client.post(
    "/admin/library/regroup",
    headers=auth,
    json={"group": "discipline-knowledge", "patterns": ["ER 1110-1-8162"], "dry_run": True},
)
check("dry run ok", resp.status_code == 200, resp.text)
check("dry run reports the move", resp.json()["would_move"] == ["ER 1110-1-8162.txt"], resp.text)
check("dry run changed nothing",
      client.get(f"/library/groups/{ENGINEERING}", headers=auth).json()["count"] >= 1)

resp = client.post(
    "/admin/library/regroup",
    headers=auth,
    json={"group": "discipline-knowledge", "patterns": ["ER 1110-1-8162"]},
)
check("regroup ok", resp.status_code == 200, resp.text)
check("regroup moved it", resp.json()["updated"] == ["ER 1110-1-8162.txt"], resp.text)
check("regroup labels the page", resp.json()["label"] == "Discipline Knowledge", resp.text)

sources = {d["source"] for d in client.get(f"/library/groups/{DISCIPLINE_KNOWLEDGE}", headers=auth).json()["documents"]}
check("regrouped document now on the discipline page", "ER 1110-1-8162.txt" in sources)
eng = {d["source"] for d in client.get(f"/library/groups/{ENGINEERING}", headers=auth).json()["documents"]}
check("and off the engineering page", "ER 1110-1-8162.txt" not in eng)

# A filename that names a publication, filed somewhere that disagrees, must stop
# presenting itself as that publication.
section("A filed document does not masquerade as the publication it names")

clear_incoming()
reset_manifest()
save_incoming_upload(text_file("x"), "AR 420-1 Class Handout.txt", "discipline-knowledge")
save_incoming_upload(text_file("x"), "ARN15118_AR 420-1_FINAL.txt", None)
run_library_ingest(library_incoming_path())

page = client.get(f"/library/groups/{DISCIPLINE_KNOWLEDGE}", headers=auth).json()
handout = next((d for d in page["documents"] if d["source"] == "AR 420-1 Class Handout.txt"), None)
check("handout is on the discipline page", handout is not None)
check("handout no longer claims the AR number", handout and handout["doc_number"] is None, str(handout)[:160])
check("handout keeps its own title", handout and "Class Handout" in (handout["display_title"] or ""))
check("handout links to the file, not the regulation",
      handout and "publications.usace.army.mil" not in (handout["url"] or ""), str(handout and handout["url"]))
check("handout link is a download", handout and "/download/" in (handout["url"] or ""), str(handout and handout["url"]))

eng = client.get(f"/library/groups/{ENGINEERING}", headers=auth).json()
real = next((d for d in eng["documents"] if d["source"] == "ARN15118_AR 420-1_FINAL.txt"), None)
check("the actual regulation is on engineering", real is not None)
check("the actual regulation keeps its number", real and real["doc_number"] == "AR 420-1", str(real)[:160])

# Both link to the local copy here because both files are on disk, and serving the
# file we hold beats sending someone to the portal. The distinction that matters is
# whether the document still claims to *be* the publication: with the number
# suppressed, nothing resolves to the official copy.
from app.downloads import document_link_url  # noqa: E402

check(
    "a real publication with no local copy resolves to the official one",
    "publications.usace.army.mil" in document_link_url("AR 420-1", "Not On Disk.pdf", upload_origin="library"),
)
check(
    "a suppressed number cannot resolve to the official one",
    "publications.usace.army.mil" not in document_link_url(None, "Not On Disk.pdf", upload_origin="library"),
)

# Moving a real publication between pages must not strip its identity, since the
# filename and the document agree about what it is.
get_rag().assign_library_group(["ARN15118_AR 420-1_FINAL.txt"], "engineering")
eng = client.get(f"/library/groups/{ENGINEERING}", headers=auth).json()
real = next((d for d in eng["documents"] if d["source"] == "ARN15118_AR 420-1_FINAL.txt"), None)
check("an agreeing assignment leaves the number intact", real and real["doc_number"] == "AR 420-1", str(real)[:160])

# The cached document list must not be damaged by display-only adjustments.
docs = get_rag().list_documents()
cached = next(d for d in docs if d["source"] == "AR 420-1 Class Handout.txt")
check("display adjustment did not mutate the cache", cached["doc_number"] == "AR 420-1", str(cached)[:160])
check("routing still sees the real number",
      {r["group"]: r["documents"] for r in group_summary(docs)}[DISCIPLINE_KNOWLEDGE] >= 1)

resp = client.post("/admin/library/regroup", headers=auth, json={"group": "engineering", "patterns": ["zzzz"]})
check("regroup with no matches is a 400", resp.status_code == 400, resp.text)
resp = client.post("/admin/library/regroup", headers=auth, json={"group": "nope", "sources": ["x.pdf"]})
check("regroup rejects an unknown page", resp.status_code == 400, resp.text)

# Non-admins must not be able to refile the library.
other = client.post("/login", json={"email": "someone.else@usace.army.mil"})
if other.status_code == 200:
    other_auth = {"Authorization": f"Bearer {other.json()['token']}"}
    resp = client.post(
        "/admin/library/regroup", headers=other_auth, json={"group": "engineering", "sources": ["x.pdf"]}
    )
    check("non-admin cannot regroup", resp.status_code in {401, 403}, resp.text)
else:
    check("non-admin cannot even sign in", other.status_code in {401, 403})


# ---------------------------------------------------------------------------
shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{PASSED} passed, {len(FAILED)} failed")
if FAILED:
    for name in FAILED:
        print(f"  - {name}")
raise SystemExit(1 if FAILED else 0)
