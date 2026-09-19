#!/usr/bin/env python3
"""Exercise the autonomous email sweep without an OpenAI key.

Stubs the model call so the sweep's real logic — windowing, message-file parsing,
meeting validation, and .ics/.eml/.md generation — is what gets tested. Run from
the repo root:

    python scripts/test_email_sweep.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ACCESS_ROSTER", "")
os.environ.setdefault("APP_API_KEY", "")

from app import email_assistant, email_sweep  # noqa: E402
from app.email_artifacts import build_ics, build_reply_eml, resolve_meeting_window, safe_filename  # noqa: E402
from app.email_messages import (  # noqa: E402
    parse_eml_file,
    parse_email_datetime,
    parse_message_file,
)
from app.outlook_connector import LocalFolderConnector  # noqa: E402

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


NOW = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)


def eml(subject: str, sender: str, *, hours_ago: int, body: str, to: str = "you@usace.army.mil") -> str:
    from email.utils import format_datetime

    sent = format_datetime(NOW - timedelta(hours=hours_ago))
    return (
        f"From: {sender}\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Date: {sent}\r\n"
        f"Message-ID: <{abs(hash(subject))}@usace.army.mil>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        f"{body}\r\n"
    )


def stub_chat(messages, *, temperature=0.35):
    """Return plausible model output so the sweep's own logic is what is tested."""
    prompt = messages[-1]["content"]
    if "Draft a reply" in prompt:
        return "Good morning,\n\nConfirmed — the review is on track.\n\nRespectfully,\nW. Pratt"
    meeting_block = (
        '"meeting": {"needed": true, "title": "Coordination call", '
        '"start": "2026-09-22T10:00", "duration_minutes": 45, '
        '"location": "Microsoft Teams", "agenda": ["Mix design data", "Review period"], '
        '"attendees": ["pm@contractor.com"], "reason": "The thread asks for a call."}'
        if "coordination" in prompt.lower()
        else '"meeting": {"needed": false}'
    )
    return (
        "{"
        '"summary": "The contractor asks about the submittal review period.",'
        '"key_points": ["Submittal 03 30 00 is under review."],'
        '"action_items": [{"action": "Confirm the review period", "owner": "you", "due": "2026-09-24"}],'
        '"open_questions": ["Is the mix design data complete?"],'
        '"deadlines": ["2026-09-24"],'
        '"priority": "high", "priority_reason": "A date is at stake.",'
        '"category": "submittal review", "reply_needed": true,'
        '"reply_needed_reason": "A direct question was asked.",'
        '"suggested_next_step": "Confirm the 21-day period.",'
        '"note": {"title": "Submittal review period", "body": "Contractor asked about timing.",'
        ' "decisions": ["21-day period applies"], "followups": ["Check mix design"]},'
        f"{meeting_block}"
        "}"
    )


def test_datetime_parsing() -> None:
    print("\nTimestamp parsing")
    check(
        "RFC 5322 Date header",
        parse_email_datetime("Tue, 16 Sep 2026 09:14:00 -0700") is not None,
    )
    check(
        "Outlook en-US 'Sent:' format",
        parse_email_datetime("Tuesday, September 16, 2026 9:14 AM") is not None,
    )
    check(
        "Outlook 'Sent:' with trailing timezone",
        parse_email_datetime("Tuesday, September 16, 2026 9:14 AM PDT") is not None,
    )
    check("slash date", parse_email_datetime("9/16/2026 9:14 AM") is not None)
    check("garbage returns None", parse_email_datetime("see attached") is None)
    check("empty returns None", parse_email_datetime("") is None)
    parsed = parse_email_datetime("Tue, 16 Sep 2026 09:14:00 -0700")
    check("result is UTC-aware", parsed is not None and parsed.tzinfo is not None)


def test_eml_parsing() -> None:
    print("\n.eml parsing")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.eml"
        path.write_text(
            eml("Submittal 03 30 00", "pm@contractor.com", hours_ago=5, body="What is the review period?"),
            encoding="utf-8",
        )
        thread = parse_eml_file(path)
        check("subject", thread.subject == "Submittal 03 30 00", thread.subject)
        check("sender address", thread.sender_address == "pm@contractor.com", thread.sender_address)
        check("body", "review period" in thread.body, thread.body)
        check("received_at set", thread.received_at is not None)
        check("message_id captured", thread.message_id.startswith("<"), thread.message_id)
        check("origin_id is the file name", thread.origin_id == "test.eml", thread.origin_id)
        check("source label", thread.source == "eml", thread.source)

        html_path = Path(tmp) / "html.eml"
        html_path.write_text(
            "From: a@b.mil\r\nTo: you@usace.army.mil\r\nSubject: HTML mail\r\n"
            "MIME-Version: 1.0\r\nContent-Type: text/html; charset=\"utf-8\"\r\n\r\n"
            "<html><body><p>First line</p><p>Second&nbsp;line</p></body></html>\r\n",
            encoding="utf-8",
        )
        html_thread = parse_eml_file(html_path)
        check("HTML converted to text", "First line" in html_thread.body, html_thread.body)
        check("HTML tags stripped", "<p>" not in html_thread.body, html_thread.body)

        check(
            "dispatcher routes .eml",
            parse_message_file(path).subject == "Submittal 03 30 00",
        )
        bad = Path(tmp) / "nope.txt"
        bad.write_text("x", encoding="utf-8")
        try:
            parse_message_file(bad)
            check("dispatcher rejects other types", False, "no error raised")
        except ValueError:
            check("dispatcher rejects other types", True)

        scrubbed_path = Path(tmp) / "pii.eml"
        scrubbed_path.write_text(
            eml("PII", "a@b.mil", hours_ago=1, body="SSN 123-45-6789 for the file."),
            encoding="utf-8",
        )
        scrubbed = parse_eml_file(scrubbed_path, scrub=True)
        check("SSN redacted", "123-45-6789" not in scrubbed.body, scrubbed.body)
        check("redaction counted", scrubbed.redactions.get("SSN") == 1, str(scrubbed.redactions))


def test_local_folder_connector() -> None:
    print("\nLocal folder connector")
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder / "recent.eml").write_text(
            eml("Recent thing", "a@b.mil", hours_ago=5, body="Inside the window."), encoding="utf-8"
        )
        (folder / "old.eml").write_text(
            eml("Old thing", "a@b.mil", hours_ago=200, body="Outside the window."), encoding="utf-8"
        )
        (folder / "notes.txt").write_text("not email", encoding="utf-8")
        for name in ("recent.eml", "old.eml"):
            target = folder / name
            os.utime(target, (NOW.timestamp(), NOW.timestamp()))

        connector = LocalFolderConnector(folder)
        check("available when folder exists", connector.available)
        check("status can_read_mailbox", connector.status()["can_read_mailbox"] is True)

        since = NOW - timedelta(hours=72)
        threads = connector.recent_threads(user_email="you@usace.army.mil", since=since)
        subjects = [t.subject for t in threads]
        check("recent message included", "Recent thing" in subjects, str(subjects))
        check("old message excluded", "Old thing" not in subjects, str(subjects))
        check("non-email file ignored", len(threads) == 1, str(subjects))

        refs = connector.list_messages(user_email="you@usace.army.mil", since=since)
        check("list_messages returns refs", len(refs) == 1 and refs[0].subject == "Recent thing")

        fetched = connector.get_thread(user_email="you@usace.army.mil", message_id="recent.eml")
        check("get_thread by id", fetched.subject == "Recent thing")

        for attempt in ("../recent.eml", "/etc/passwd", "sub/recent.eml"):
            try:
                connector.get_thread(user_email="you@usace.army.mil", message_id=attempt)
                check(f"path traversal refused: {attempt}", False, "no error raised")
            except Exception:
                check(f"path traversal refused: {attempt}", True)

        missing = LocalFolderConnector(folder / "does-not-exist")
        check("missing folder unavailable", not missing.available)
        check("missing folder explains itself", bool(missing.missing_requirements()))


def test_artifacts() -> None:
    print("\nArtifact generation")
    start = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    meeting = {
        "title": "Coordination call; mix design, review",
        "start": start,
        "end": start + timedelta(minutes=45),
        "location": "Microsoft Teams",
        "agenda": ["Mix design data", "Review period"],
        "description": "The thread asks for a call.",
        "reminder_minutes": 15,
    }
    ics = build_ics(meeting, organizer="you@usace.army.mil", attendees=["pm@contractor.com"])
    check("ics wrapped in VCALENDAR", ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR"))
    check("ics uses CRLF", "\r\n" in ics and "\n\n" not in ics.replace("\r\n", "\n\n").replace("\n\n", "\r\n"))
    check("ics has DTSTART in UTC", "DTSTART:20260922T100000Z" in ics, ics)
    check("ics has DTEND", "DTEND:20260922T104500Z" in ics)
    check("semicolons escaped in SUMMARY", "Coordination call\\; mix design\\, review" in ics, ics)
    check("attendee present", "ATTENDEE" in ics and "mailto:pm@contractor.com" in ics)
    check("method REQUEST with attendees", "METHOD:REQUEST" in ics)
    check("status tentative", "STATUS:TENTATIVE" in ics)
    check("alarm included", "BEGIN:VALARM" in ics and "TRIGGER:-PT15M" in ics)
    check("draft notice in description", "nothing has been sent" in ics.replace("\r\n ", ""))

    solo = build_ics({**meeting, "agenda": []}, organizer="you@usace.army.mil", attendees=[])
    check("method PUBLISH without attendees", "METHOD:PUBLISH" in solo)

    long_meeting = {**meeting, "title": "A" * 200}
    folded = build_ics(long_meeting, organizer="you@usace.army.mil")
    check(
        "long lines folded under 75 octets",
        all(len(line.encode()) <= 75 for line in folded.split("\r\n")),
        max(folded.split("\r\n"), key=len),
    )
    check("unfolding restores the text", "A" * 200 in folded.replace("\r\n ", ""))

    try:
        build_ics({"title": "No time"}, organizer="you@usace.army.mil")
        check("ics without a time is refused", False, "no error raised")
    except ValueError:
        check("ics without a time is refused", True)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.eml"
        path.write_text(
            eml(
                "Submittal 03 30 00",
                "pm@contractor.com",
                hours_ago=5,
                body="What is the review period?",
                to="you@usace.army.mil, qa@usace.army.mil",
            ),
            encoding="utf-8",
        )
        thread = parse_eml_file(path)

    draft = {"subject": "RE: Submittal 03 30 00", "body": "Confirmed — 21 days."}
    raw = build_reply_eml(draft, thread, from_address="you@usace.army.mil")
    check("eml has X-Unsent for Outlook compose", "X-Unsent: 1" in raw, raw[:400])
    check("replies to the sender", "To: pm@contractor.com" in raw, raw[:400])
    check("keeps other recipients on Cc", "qa@usace.army.mil" in raw, raw[:400])

    check("crlf line endings", "\r\n" in raw)
    check("threads with In-Reply-To", "In-Reply-To:" in raw)
    check("subject carried over", "Subject: RE: Submittal 03 30 00" in raw)

    # Check the decoded body rather than the raw MIME, so the assertion holds
    # regardless of the transfer encoding chosen for non-ASCII text.
    from email import message_from_string, policy as eml_policy

    reparsed = message_from_string(raw, policy=eml_policy.default)
    decoded = reparsed.get_content()
    check("user is not a recipient of their own reply",
          "you@usace.army.mil" not in f"{reparsed['To']} {reparsed['Cc']}",
          f"To={reparsed['To']} Cc={reparsed['Cc']}")
    check("reply goes to the original sender", reparsed["To"] == "pm@contractor.com", str(reparsed["To"]))
    check("other recipient on Cc", reparsed["Cc"] == "qa@usace.army.mil", str(reparsed["Cc"]))
    check("body survives a round trip", "Confirmed — 21 days." in decoded, decoded[:200])
    check("quotes the original", "-----Original Message-----" in decoded, decoded[:400])
    check("original body quoted", "What is the review period?" in decoded, decoded[:400])

    check("filename sanitized", safe_filename("RE: Sub/mittal 03*30", ".eml") == "RE-Sub-mittal-03-30.eml",
          safe_filename("RE: Sub/mittal 03*30", ".eml"))
    check("filename never empty", safe_filename("///", ".ics") == "project-spk.ics", safe_filename("///", ".ics"))

    s, e = resolve_meeting_window(start, duration_minutes=None, end=None)
    check("default duration applied", (e - s) == timedelta(minutes=30))
    s, e = resolve_meeting_window(start, duration_minutes=60, end=start - timedelta(hours=1))
    check("end before start corrected", e > s and (e - s) == timedelta(minutes=60))
    s, e = resolve_meeting_window(start, duration_minutes=99_999, end=start + timedelta(days=9))
    check("absurd duration clamped", (e - s) <= timedelta(hours=8), str(e - s))


def test_meeting_validation() -> None:
    print("\nMeeting proposal validation")
    from app.email_messages import EmailThread, EmailTurn

    tz = timezone.utc
    thread = EmailThread(subject="Coordination", to="a@b.mil")
    now = NOW

    none_needed = email_assistant._normalize_meeting({"needed": False}, thread, tz=tz, now=now)
    check("no meeting when not needed", none_needed is None)

    future = email_assistant._normalize_meeting(
        {"needed": True, "title": "Call", "start": "2026-09-22T10:00", "duration_minutes": 45},
        thread,
        tz=tz,
        now=now,
    )
    check("future time accepted", future is not None and future["time_known"] is True)
    check("duration honored", future is not None and (future["end"] - future["start"]) == timedelta(minutes=45))

    past = email_assistant._normalize_meeting(
        {"needed": True, "title": "Call", "start": "2020-01-01T10:00"}, thread, tz=tz, now=now
    )
    check("past time rejected but proposal kept", past is not None and past["time_known"] is False)
    check("no start when time unusable", past is not None and past["start"] is None)

    unparseable = email_assistant._normalize_meeting(
        {"needed": True, "title": "Call", "start": "sometime next week"}, thread, tz=tz, now=now
    )
    check("unparseable time flagged", unparseable is not None and unparseable["time_known"] is False)

    on_thread = EmailThread(
        subject="Coordination",
        turns=[EmailTurn(sender="dana@usace.army.mil", to="you@usace.army.mil, ok@usace.army.mil")],
    )
    attendees = email_assistant._normalize_meeting(
        {
            "needed": True,
            "start": "2026-09-22T10:00",
            "attendees": ["Not an address", "ok@usace.army.mil"],
        },
        on_thread,
        tz=tz,
        now=now,
    )
    check("non-addresses dropped", attendees is not None and attendees["attendees"] == ["ok@usace.army.mil"],
          str(attendees and attendees["attendees"]))

    # An agent that invents an invitee, or that quietly invites everyone on a
    # thread to what should be a private time block, causes real harm.
    invented = email_assistant._normalize_meeting(
        {"needed": True, "start": "2026-09-22T10:00", "attendees": ["stranger@example.com"]},
        on_thread,
        tz=tz,
        now=now,
    )
    check("addresses not on the thread are refused",
          invented is not None and invented["attendees"] == [], str(invented and invented["attendees"]))

    block_time = email_assistant._normalize_meeting(
        {"needed": True, "title": "Block time", "start": "2026-09-22T10:00", "attendees": []},
        on_thread,
        tz=tz,
        now=now,
    )
    check("empty attendee list stays empty",
          block_time is not None and block_time["attendees"] == [], str(block_time and block_time["attendees"]))
    solo_ics = build_ics(
        {**block_time, "description": ""}, organizer="you@usace.army.mil", attendees=block_time["attendees"]
    )
    check("private appointment sends no invitations", "ATTENDEE" not in solo_ics)
    check("private appointment is PUBLISH not REQUEST", "METHOD:PUBLISH" in solo_ics)


def test_windowing() -> None:
    print("\nWindow selection")
    from app.email_messages import EmailThread

    since = NOW - timedelta(hours=72)
    threads = [
        EmailThread(subject="fresh", received_at=NOW - timedelta(hours=2)),
        EmailThread(subject="edge", received_at=NOW - timedelta(hours=71)),
        EmailThread(subject="stale", received_at=NOW - timedelta(hours=100)),
        EmailThread(subject="undated", received_at=None),
    ]
    selected, skipped = email_sweep.select_threads(threads, since=since)
    subjects = [t.subject for t in selected]
    check("in-window kept", "fresh" in subjects and "edge" in subjects)
    check("out-of-window dropped", "stale" not in subjects, str(subjects))
    check("undated kept rather than guessed", "undated" in subjects, str(subjects))
    check("skipped counted", skipped == 1, str(skipped))
    check("newest first", subjects[0] in ("fresh", "undated"), str(subjects))

    capped, _ = email_sweep.select_threads(threads, since=since, limit=2)
    check("limit respected", len(capped) == 2)

    since_b, until_b, hours = email_sweep.window_bounds(72, now=NOW)
    check("window is 72h by request", hours == 72 and (until_b - since_b) == timedelta(hours=72))
    _, _, clamped = email_sweep.window_bounds(99_999, now=NOW)
    check("oversized window clamped", clamped == 336, str(clamped))
    _, _, floor = email_sweep.window_bounds(0, now=NOW)
    check("zero falls back to the default", floor == 72, str(floor))


def test_full_sweep() -> None:
    print("\nFull sweep with a stubbed model")
    original = email_assistant.chat_completion
    email_assistant.chat_completion = stub_chat
    try:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.eml").write_text(
                eml("Coordination call needed", "pm@contractor.com", hours_ago=4,
                    body="Can we get on a call Thursday?"),
                encoding="utf-8",
            )
            (folder / "b.eml").write_text(
                eml("Submittal 03 30 00", "qa@contractor.com", hours_ago=30,
                    body="What is the review period?"),
                encoding="utf-8",
            )
            for name in ("a.eml", "b.eml"):
                os.utime(folder / name, (NOW.timestamp(), NOW.timestamp()))

            connector = LocalFolderConnector(folder)
            threads = connector.recent_threads(
                user_email="you@usace.army.mil", since=NOW - timedelta(hours=72)
            )
            check("both messages collected", len(threads) == 2, str(len(threads)))

            seen: list[tuple[str, int, int]] = []
            report = email_sweep.run_sweep(
                threads,
                user_email="you@usace.army.mil",
                user_name="W. Pratt",
                source="local_folder",
                hours=72,
                now=NOW,
                progress=lambda phase, done, total, detail: seen.append((phase, done, total)),
            )

            check("all analyzed", report.messages_analyzed == 2, str(report.messages_analyzed))
            check("none failed", report.messages_failed == 0, str(report.messages_failed))
            check("progress reported", any(p == "analyze" for p, _, _ in seen), str(seen))
            check("draft phase reported", any(p == "draft" for p, _, _ in seen), str(seen))
            check("done reported last", seen[-1][0] == "done", str(seen[-1]))

            data = report.to_dict()
            check("report serializes", isinstance(data, dict) and len(data["items"]) == 2)
            digest = data["digest"]
            check("digest counts priorities", digest["priority_counts"]["high"] == 2, str(digest["priority_counts"]))
            check("digest counts drafts", digest["replies_drafted"] == 2, str(digest["replies_drafted"]))
            check("digest counts notes", digest["notes_written"] == 2)
            check("digest lists deadlines", len(digest["deadlines"]) == 2, str(digest["deadlines"]))
            check("digest lists reply-needed", len(digest["needs_reply"]) == 2)

            kinds = {a["kind"] for item in data["items"] for a in item["artifacts"]}
            check("reply artifact built", "reply" in kinds, str(kinds))
            check("note artifact built", "note" in kinds, str(kinds))
            check("invite artifact built", "invite" in kinds, str(kinds))

            invite = next(
                a for item in data["items"] for a in item["artifacts"] if a["kind"] == "invite"
            )
            check("invite is valid ics", invite["content"].startswith("BEGIN:VCALENDAR"))
            check("invite filename ends .ics", invite["filename"].endswith(".ics"), invite["filename"])
            reply = next(a for item in data["items"] for a in item["artifacts"] if a["kind"] == "reply")
            check("reply is unsent eml", "X-Unsent: 1" in reply["content"])
            note = next(a for item in data["items"] for a in item["artifacts"] if a["kind"] == "note")
            check("note is markdown", note["content"].startswith("# "), note["content"][:60])
            check("note lists action items", "Action items" in note["content"])

            only_one = next(item for item in data["items"] if "Submittal" in item["email"]["subject"])
            check("non-meeting mail has no invite",
                  not any(a["kind"] == "invite" for a in only_one["artifacts"]),
                  str([a["kind"] for a in only_one["artifacts"]]))

            no_extras = email_sweep.run_sweep(
                threads, user_email="you@usace.army.mil", hours=72, now=NOW,
                draft_replies=False, write_notes=False, build_invites=False,
            )
            check("drafts can be turned off", no_extras.digest()["replies_drafted"] == 0)
            check("notes can be turned off", no_extras.digest()["notes_written"] == 0)
            check("invites can be turned off", no_extras.digest()["invites_built"] == 0)
    finally:
        email_assistant.chat_completion = original


def test_sweep_resilience() -> None:
    print("\nResilience")
    from app.email_messages import EmailThread

    calls = {"n": 0}

    def flaky(messages, *, temperature=0.35):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model timed out")
        return stub_chat(messages, temperature=temperature)

    original = email_assistant.chat_completion
    email_assistant.chat_completion = flaky
    try:
        threads = [
            EmailThread(subject="first", body="x", turns=[], received_at=NOW - timedelta(hours=1)),
            EmailThread(subject="second", body="y", turns=[], received_at=NOW - timedelta(hours=2)),
        ]
        report = email_sweep.run_sweep(threads, user_email="you@usace.army.mil", hours=72, now=NOW)
        check("one failure recorded", report.messages_failed == 1, str(report.messages_failed))
        check("sweep continued past it", report.messages_analyzed == 1, str(report.messages_analyzed))
        failed = [i for i in report.items if i.error]
        check("error message is readable", "model timed out" in failed[0].error, failed[0].error)
        check("report still serializes", isinstance(report.to_dict(), dict))
    finally:
        email_assistant.chat_completion = original


def test_model_endpoint_info() -> None:
    print("\nModel endpoint reporting")
    from app.config import settings as s
    from app.llm import model_endpoint_info

    original = s.openai_base_url
    try:
        s.openai_base_url = ""
        info = model_endpoint_info()
        check("default endpoint is public OpenAI", info["public_openai"] is True)
        check("default is not self-hosted", info["self_hosted"] is False)

        s.openai_base_url = "http://localhost:8001/v1"
        info = model_endpoint_info()
        check("local endpoint host parsed", info["endpoint_host"] == "localhost", str(info))
        check("local endpoint is self-hosted", info["self_hosted"] is True)
        check("local endpoint flagged local", info["local"] is True)

        s.openai_base_url = "https://llm.spk.usace.army.mil/v1"
        info = model_endpoint_info()
        check("on-prem host parsed", info["endpoint_host"] == "llm.spk.usace.army.mil", str(info))
        check("on-prem is self-hosted", info["self_hosted"] is True)
        check("on-prem is not flagged local", info["local"] is False)
    finally:
        s.openai_base_url = original


def main() -> int:
    print("Project SPK — autonomous email sweep tests")
    test_datetime_parsing()
    test_eml_parsing()
    test_local_folder_connector()
    test_artifacts()
    test_meeting_validation()
    test_windowing()
    test_full_sweep()
    test_sweep_resilience()
    test_model_endpoint_info()

    print(f"\n{PASSED} passed, {len(FAILED)} failed")
    if FAILED:
        for label in FAILED:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
