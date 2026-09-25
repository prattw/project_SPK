"""Review Desk — local ProjNet comment and backcheck drafts.

This is not Project SPK. It listens only on this Mac and talks only to a
local model. ProjNet remains the system of record; paste drafts back into it.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from drafts import LocalModelRequired, assert_local_model, draft_text
from publications import check_text
from store import ReviewStore
from xml_import import parse_review_xml

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    drchecks_base_url: str = "http://127.0.0.1:11434/v1"
    drchecks_api_key: str = "ollama"
    drchecks_model: str = "qwen2.5:7b-instruct"


settings = Settings()
store = ReviewStore()
app = FastAPI(title="Review Desk", version="0.1.0")


class DraftRequest(BaseModel):
    kind: str = Field(..., pattern="^(comment|evaluation|backcheck)$")
    note: str = ""
    discipline: str = ""
    document: str = ""
    section: str = ""
    comment: str = ""


class NewComment(BaseModel):
    discipline: str = ""
    document: str = ""
    section: str = ""
    comment: str = ""
    note: str = ""


def _facts_for(record: dict) -> list[dict[str, str]]:
    text = "\n".join(
        str(record.get(key) or "")
        for key in ("comment", "response", "backcheck", "section", "document", "note")
    )
    return check_text(text)


@app.get("/health")
def health() -> dict:
    local = True
    try:
        assert_local_model(settings.drchecks_base_url)
    except LocalModelRequired:
        local = False
    return {
        "status": "ok",
        "app": "review-desk",
        "model": settings.drchecks_model,
        "local_model": local,
        "comments": len(store.list_comments()),
    }


@app.get("/comments")
def list_comments() -> dict:
    return {"comments": store.list_comments()}


@app.get("/comments/{comment_id}")
def get_comment(comment_id: str) -> dict:
    record = store.get(comment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That comment is not in this review.")
    return {"comment": record, "publications": _facts_for(record)}


@app.post("/import")
async def import_xml(file: UploadFile = File(...)) -> dict:
    name = file.filename or "upload.xml"
    if not name.lower().endswith(".xml"):
        raise HTTPException(status_code=400, detail="Export the DrChecks report as XML.")
    data = await file.read()
    try:
        records = parse_review_xml(data, source_file=name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.replace_file(name, records)
    return {"imported": len(records), "source_file": name, "comments": records}


@app.post("/comments/new")
def draft_new(body: NewComment) -> dict:
    record = {
        "id": "new",
        "discipline": body.discipline,
        "document": body.document,
        "section": body.section,
        "comment": body.comment or body.note,
        "response": "",
        "backcheck": "",
        "status": "Draft",
        "note": body.note,
    }
    if not (record["comment"] or body.note):
        raise HTTPException(status_code=400, detail="Write the issue you want the comment to cover.")
    facts = _facts_for(record)
    try:
        text = draft_text(
            record,
            "comment",
            body.note,
            facts,
            base_url=settings.drchecks_base_url,
            api_key=settings.drchecks_api_key,
            model=settings.drchecks_model,
        )
    except LocalModelRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The local model did not answer. Is Ollama running? {exc}",
        ) from exc
    return {"draft": text, "publications": facts}


@app.post("/comments/{comment_id}/draft")
def draft_existing(comment_id: str, body: DraftRequest) -> dict:
    record = store.get(comment_id)
    if record is None:
        raise HTTPException(status_code=404, detail="That comment is not in this review.")
    working = dict(record)
    for key in ("discipline", "document", "section", "comment"):
        if getattr(body, key):
            working[key] = getattr(body, key)
    working["note"] = body.note
    facts = _facts_for(working)
    try:
        text = draft_text(
            working,
            body.kind,
            body.note,
            facts,
            base_url=settings.drchecks_base_url,
            api_key=settings.drchecks_api_key,
            model=settings.drchecks_model,
        )
    except LocalModelRequired as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"The local model did not answer. Is Ollama running? {exc}",
        ) from exc
    return {"draft": text, "publications": facts, "kind": body.kind}


@app.get("/")
def index():
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return {"message": "Review Desk UI is missing."}
    return FileResponse(page)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    host = os.environ.get("DRCHECKS_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Review Desk listens on 127.0.0.1 only.")
    port = int(os.environ.get("DRCHECKS_PORT", "8010"))
    uvicorn.run("server:app", host=host, port=port, reload=False)
