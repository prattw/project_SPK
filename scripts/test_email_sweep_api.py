#!/usr/bin/env python3
"""Exercise the sweep HTTP endpoints with a stubbed model.

Uses FastAPI's TestClient so the real routing, validation, job plumbing, and
owner-scoping are what get tested. Run from the repo root:

    python scripts/test_email_sweep_api.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Auth off so the tests exercise the endpoints rather than the login gate.
os.environ["ACCESS_ROSTER"] = ""
os.environ["APP_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = "test-key-not-used"

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def stub_chat(messages, *, temperature=0.35):
    prompt = messages[-1]["content"]
    if "Draft a reply" in prompt:
        return "Good morning,\n\nConfirmed.\n\nRespectfully,\nW. Pratt"
    return (
        '{"summary": "Contractor asks about the review period.",'
        '"key_points": ["Submittal under review."],'
        '"action_items": [{"action": "Confirm the period", "owner": "you", "due": "2026-09-24"}],'
        '"open_questions": [], "deadlines": ["2026-09-24"],'
        '"priority": "high", "priority_reason": "A date is at stake.",'
        '"category": "submittal review", "reply_needed": true,'
        '"reply_needed_reason": "A question was asked.",'
        '"suggested_next_step": "Confirm the period.",'
        '"note": {"title": "Review period", "body": "Asked about timing.", "decisions": [], "followups": []},'
        '"meeting": {"needed": true, "title": "Coordination call", "start": "2099-01-05T10:00",'
        ' "duration_minutes": 30, "location": "Teams", "agenda": ["Timing"],'
        ' "attendees": ["pm@contractor.com"], "reason": "A call was requested."}}'
    )


def message_bytes(subject: str, sender: str, *, hours_ago: int) -> bytes:
    sent = format_datetime(datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    return (
        f"From: {sender}\r\nTo: you@usace.army.mil\r\nSubject: {subject}\r\n"
        f"Date: {sent}\r\nMessage-ID: <{abs(hash(subject))}@x>\r\n"
        'MIME-Version: 1.0\r\nContent-Type: text/plain; charset="utf-8"\r\n\r\n'
        "What is the review period?\r\n"
    ).encode()


def wait_for_job(client, job_id: str, *, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    job: dict = {}
    while time.time() < deadline:
        response = client.get(f"/jobs/{job_id}")
        if response.status_code != 200:
            return {"status": "http_error", "code": response.status_code, "body": response.json()}
        job = response.json()
        if job.get("status") in {"done", "error"}:
            return job
        time.sleep(0.2)
    return job


def main() -> int:
    from fastapi.testclient import TestClient

    from app import email_assistant, llm
    from app.config import settings

    email_assistant.chat_completion = stub_chat

    from app.main import app

    client = TestClient(app)
    print("Project SPK — sweep API tests")

    print("\nGET /email/status")
    status = client.get("/email/status")
    check("returns 200", status.status_code == 200, str(status.status_code))
    data = status.json()
    check("reports sweep config", "sweep" in data, str(list(data)))
    sweep = data.get("sweep", {})
    check("default window is 72 hours", sweep.get("window_hours") == 72, str(sweep.get("window_hours")))
    check("message cap reported", sweep.get("max_messages") == 40, str(sweep.get("max_messages")))
    check("manual connector cannot read a mailbox", sweep.get("can_read_mailbox") is False, str(sweep))
    check("autostart off without a readable source", sweep.get("autostart") is False, str(sweep))
    check("model endpoint reported", "model" in data and "endpoint_host" in data["model"], str(data.get("model")))
    check("upload suffixes advertised", set(data.get("upload_suffixes") or []) == {".msg", ".eml"},
          str(data.get("upload_suffixes")))
    mailbox = data.get("mailbox", {})
    check("alternatives listed for other connectors", len(mailbox.get("alternatives") or []) == 2,
          str([a.get("connector") for a in mailbox.get("alternatives") or []]))

    print("\nPOST /email/sweep with no readable source")
    blocked = client.post("/email/sweep", json={})
    check("returns 503", blocked.status_code == 503, str(blocked.status_code))
    detail = blocked.json().get("detail", {})
    check("explains why", "cannot sweep" in str(detail.get("message", "")), str(detail))
    check("lists what is needed", bool(detail.get("requirements")), str(detail))

    print("\nPOST /email/sweep/upload")
    files = [
        ("files", ("a.eml", message_bytes("Submittal 03 30 00", "pm@contractor.com", hours_ago=4), "message/rfc822")),
        ("files", ("b.eml", message_bytes("RFI 014 response", "qa@contractor.com", hours_ago=30), "message/rfc822")),
    ]
    started = client.post("/email/sweep/upload", files=files, data={"tone": "professional"})
    check("returns 200", started.status_code == 200, started.text[:300])
    job_id = started.json().get("job_id", "")
    check("returns a job id", bool(job_id))

    job = wait_for_job(client, job_id)
    check("job completes", job.get("status") == "done", str(job.get("status")) + " " + str(job.get("message")))
    report = job.get("sweep_report") or {}
    check("report attached to the job", bool(report.get("items")), str(list(report)))
    check("both messages analyzed", report.get("messages_analyzed") == 2, str(report.get("messages_analyzed")))
    check("source recorded as upload", report.get("source") == "upload", str(report.get("source")))

    digest = report.get("digest") or {}
    check("digest counts high priority", digest.get("priority_counts", {}).get("high") == 2, str(digest))
    check("digest counts replies", digest.get("replies_drafted") == 2, str(digest.get("replies_drafted")))
    check("digest counts invites", digest.get("invites_built") == 2, str(digest.get("invites_built")))

    kinds = {a["kind"] for item in report["items"] for a in item["artifacts"]}
    check("reply/note/invite artifacts present", {"reply", "note", "invite"} <= kinds, str(kinds))
    invite = next(a for item in report["items"] for a in item["artifacts"] if a["kind"] == "invite")
    check("invite content is iCalendar", invite["content"].startswith("BEGIN:VCALENDAR"))
    check("invite mime type set", invite["mime"] == "text/calendar", invite["mime"])
    reply = next(a for item in report["items"] for a in item["artifacts"] if a["kind"] == "reply")
    check("reply is an unsent draft", "X-Unsent: 1" in reply["content"])
    check("job progress counted messages", job.get("files_total") == 2, str(job.get("files_total")))

    print("\nUpload validation")
    bad_type = client.post("/email/sweep/upload", files=[("files", ("notes.txt", b"hello", "text/plain"))])
    check("non-email files rejected", bad_type.status_code == 400, str(bad_type.status_code))
    check("names the problem", "could be read" in str(bad_type.json().get("detail", {})), bad_type.text[:200])

    empty = client.post("/email/sweep/upload", files=[("files", ("a.eml", b"", "message/rfc822"))])
    check("empty files rejected", empty.status_code == 400, str(empty.status_code))

    original_cap = settings.email_sweep_max_messages
    settings.email_sweep_max_messages = 1
    try:
        too_many = client.post(
            "/email/sweep/upload",
            files=[
                ("files", ("a.eml", message_bytes("A", "a@b.mil", hours_ago=1), "message/rfc822")),
                ("files", ("b.eml", message_bytes("B", "a@b.mil", hours_ago=1), "message/rfc822")),
            ],
        )
        check("over-cap batch rejected", too_many.status_code == 413, str(too_many.status_code))
        check("cap explained", "limit" in str(too_many.json().get("detail", "")), too_many.text[:200])
    finally:
        settings.email_sweep_max_messages = original_cap

    print("\nLocal folder connector over HTTP")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "recent.eml").write_bytes(message_bytes("Folder sweep", "pm@contractor.com", hours_ago=6))
        (folder / "ancient.eml").write_bytes(message_bytes("Too old", "pm@contractor.com", hours_ago=500))

        original_connector = settings.outlook_connector
        original_folder = settings.outlook_local_folder
        settings.outlook_connector = "local_folder"
        settings.outlook_local_folder = str(folder)
        try:
            status2 = client.get("/email/status").json()
            check("folder source can read mail", status2["sweep"]["can_read_mailbox"] is True, str(status2["sweep"]))
            check("autostart enabled with a readable source", status2["sweep"]["autostart"] is True,
                  str(status2["sweep"]))

            preview = client.get("/email/mailbox/messages")
            check("mailbox preview returns 200", preview.status_code == 200, preview.text[:200])
            preview_data = preview.json()
            check("preview windows out old mail", preview_data["count"] == 1, str(preview_data))
            check("preview shows the subject",
                  preview_data["messages"][0]["subject"] == "Folder sweep", str(preview_data["messages"]))

            auto = client.post("/email/sweep", json={})
            check("autonomous sweep starts", auto.status_code == 200, auto.text[:300])
            auto_job = wait_for_job(client, auto.json()["job_id"])
            check("autonomous sweep completes", auto_job.get("status") == "done",
                  f"{auto_job.get('status')}: {auto_job.get('message')}")
            auto_report = auto_job.get("sweep_report") or {}
            check("only in-window mail swept", auto_report.get("messages_analyzed") == 1,
                  str(auto_report.get("messages_analyzed")))
            check("source recorded as local_folder", auto_report.get("source") == "local_folder",
                  str(auto_report.get("source")))

            narrow = client.post("/email/sweep", json={"hours": 2})
            narrow_job = wait_for_job(client, narrow.json()["job_id"])
            check("narrow window finds nothing", (narrow_job.get("sweep_report") or {}).get("empty") is True,
                  str(narrow_job.get("message")))
            check("empty sweep still succeeds", narrow_job.get("status") == "done", str(narrow_job.get("status")))
        finally:
            settings.outlook_connector = original_connector
            settings.outlook_local_folder = original_folder

    print("\nSweep results are private to the user who ran them")
    from app.jobs import get_job

    owned = get_job(job_id)
    check("sweep job records an owner", owned is not None and owned.kind == "email_sweep")
    owned.owner_email = "someone.else@usace.army.mil"
    denied = client.get(f"/jobs/{job_id}")
    check("another user gets 404", denied.status_code == 404, str(denied.status_code))
    check("no content leaked", "sweep_report" not in denied.text, denied.text[:200])
    owned.owner_email = None
    check("ownerless jobs stay readable", client.get(f"/jobs/{job_id}").status_code == 200)

    print("\nFeature flags")
    original_sweep = settings.email_sweep_enabled
    settings.email_sweep_enabled = False
    try:
        off = client.post("/email/sweep", json={})
        check("disabled sweep returns 503", off.status_code == 503, str(off.status_code))
        check("names the setting", "EMAIL_SWEEP_ENABLED" in off.text, off.text[:200])
        check("status still reports it", client.get("/email/status").json()["sweep"]["enabled"] is False)
        check("single-email analyze still works", client.post(
            "/email/analyze", json={"text": "From: a@b.mil\nSubject: Hi\n\nQuestion?"}
        ).status_code == 200)
    finally:
        settings.email_sweep_enabled = original_sweep

    original_assistant = settings.email_assistant_enabled
    settings.email_assistant_enabled = False
    try:
        off = client.post("/email/sweep", json={})
        check("disabled assistant blocks the sweep too", off.status_code == 503, str(off.status_code))
    finally:
        settings.email_assistant_enabled = original_assistant

    print("\nSelf-hosted model reporting over HTTP")
    original_base = settings.openai_base_url
    settings.openai_base_url = "https://llm.spk.usace.army.mil/v1"
    try:
        model = client.get("/email/status").json()["model"]
        check("host surfaced", model["endpoint_host"] == "llm.spk.usace.army.mil", str(model))
        check("flagged self-hosted", model["self_hosted"] is True, str(model))
        check("not flagged public OpenAI", model["public_openai"] is False, str(model))
    finally:
        settings.openai_base_url = original_base
    check("llm module exposes endpoint info", callable(llm.model_endpoint_info))

    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        for label in FAILED:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
