"""Turn sweep output into files Outlook already knows how to open.

The autonomous sweep produces three kinds of artifact, and none of them is a
proprietary format:

======================  ========  =================================================
Artifact                Format    What the user does with it
======================  ========  =================================================
Reply draft             ``.eml``  Opens in Outlook as an unsent message, ready to
                                  edit and send (``X-Unsent: 1``).
Appointment / invite    ``.ics``  Double-click to open the appointment or meeting
                                  request in Outlook, then save or send.
Note for the record     ``.md``   Paste into OneNote, a project file, or RMS.
======================  ========  =================================================

Nothing here transmits anything. Each function returns file *content* as a
string; the browser saves it locally, and the user chooses whether to send. That
keeps the invariant the whole email feature is built on: Project SPK drafts, a
human sends.

The ``.ics`` output follows RFC 5545 — CRLF line endings, lines folded at 75
octets, and ``TEXT`` values escaped — because Outlook silently refuses files that
do not.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, getaddresses
from typing import Any

from app.email_messages import EmailThread

_PRODID = "-//Project SPK//USACE Email Assistant//EN"

# Outlook reads this header and opens the file as a composable draft instead of
# a received message. It is the only reliable way to hand someone an editable
# reply without touching their mailbox.
_UNSENT_HEADER = ("X-Unsent", "1")

_DRAFT_NOTICE = (
    "Drafted by Project SPK from the email thread. Review the time, attendees, and "
    "agenda before sending — nothing has been sent or added to a calendar."
)

_ICS_ESCAPE = str.maketrans(
    {
        "\\": "\\\\",
        ";": "\\;",
        ",": "\\,",
        "\n": "\\n",
    }
)


def _fold(line: str) -> str:
    """Fold one content line to 75 octets per RFC 5545 section 3.1."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks: list[bytes] = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        # Never split a multi-byte character across a fold boundary.
        while end > start and end < len(raw) and (raw[end] & 0xC0) == 0x80:
            end -= 1
        chunks.append(raw[start:end])
        start = end
        limit = 74  # continuation lines carry a leading space
    return "\r\n ".join(chunk.decode("utf-8") for chunk in chunks)


def _ics_text(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").translate(_ICS_ESCAPE)


def _ics_stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _address_list(value: str) -> list[str]:
    """Extract addresses from a header value, preserving order and dropping dupes."""
    seen: dict[str, None] = {}
    for _, address in getaddresses([value or ""]):
        address = (address or "").strip().lower()
        if "@" in address:
            seen.setdefault(address, None)
    return list(seen)


def build_ics(
    meeting: dict[str, Any],
    *,
    organizer: str = "",
    attendees: list[str] | None = None,
    uid: str | None = None,
    now: datetime | None = None,
) -> str:
    """Build an iCalendar appointment or meeting request.

    ``meeting`` is the normalized proposal from
    :func:`app.email_assistant.analyze_for_sweep` — it must already carry an
    aware ``start`` and ``end``. With attendees the result is a ``REQUEST`` that
    Outlook opens as a sendable invitation; without them it is a ``PUBLISH``
    appointment for the user's own calendar. Either way it is ``TENTATIVE``,
    since the time was inferred from an email rather than agreed to.
    """
    start = meeting.get("start")
    end = meeting.get("end")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise ValueError("A calendar invite needs resolved start and end datetimes.")

    invitees = [a for a in (attendees or []) if a and a != (organizer or "").lower()]
    method = "REQUEST" if invitees else "PUBLISH"
    description = meeting.get("description") or ""
    agenda = meeting.get("agenda") or []
    if agenda:
        description = (description + "\n\nAgenda:\n" + "\n".join(f"- {item}" for item in agenda)).strip()
    description = f"{description}\n\n{_DRAFT_NOTICE}".strip()

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{uid or uuid.uuid4()}@project-spk",
        f"DTSTAMP:{_ics_stamp(now or datetime.now(timezone.utc))}",
        f"DTSTART:{_ics_stamp(start)}",
        f"DTEND:{_ics_stamp(end)}",
        f"SUMMARY:{_ics_text(meeting.get('title') or 'Meeting')}",
        f"DESCRIPTION:{_ics_text(description)}",
        "STATUS:TENTATIVE",
        "SEQUENCE:0",
        "TRANSP:OPAQUE",
    ]
    if meeting.get("location"):
        lines.append(f"LOCATION:{_ics_text(str(meeting['location']))}")
    if organizer:
        lines.append(f"ORGANIZER;CN={_ics_text(organizer)}:mailto:{organizer}")
    for address in invitees:
        lines.append(
            "ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE"
            f";CN={_ics_text(address)}:mailto:{address}"
        )
    if meeting.get("reminder_minutes"):
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_ics_text(meeting.get('title') or 'Meeting')}",
                f"TRIGGER:-PT{int(meeting['reminder_minutes'])}M",
                "END:VALARM",
            ]
        )
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def build_reply_eml(
    draft: dict[str, Any],
    thread: EmailThread,
    *,
    from_address: str = "",
    now: datetime | None = None,
) -> str:
    """Build an unsent ``.eml`` reply Outlook opens in compose mode.

    Recipients come from the thread being answered: the original sender goes on
    ``To``, everyone else who was on it goes on ``Cc``, and the user's own address
    is removed so they do not reply to themselves. ``In-Reply-To``/``References``
    keep Outlook's conversation view intact when the reply is sent.
    """
    message = EmailMessage(policy=SMTP)
    sender = thread.sender_address
    me = (from_address or "").strip().lower()

    to = [a for a in ([sender] if sender else []) if a != me]
    cc = [
        a
        for a in _address_list(f"{thread.to}, {thread.cc}")
        if a != me and a not in to
    ]

    if from_address:
        message["From"] = from_address
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = draft.get("subject") or thread.subject or "(no subject)"
    message["Date"] = format_datetime(now or datetime.now(timezone.utc))
    if thread.message_id:
        message["In-Reply-To"] = thread.message_id
        message["References"] = thread.message_id
    message[_UNSENT_HEADER[0]] = _UNSENT_HEADER[1]
    message["X-Project-SPK-Draft"] = "reply"

    body = (draft.get("body") or "").strip()
    quoted = _quote_original(thread)
    # Quoted-printable keeps the file legible in a text editor, which base64 does
    # not, while still encoding the non-ASCII characters drafts routinely contain.
    message.set_content(f"{body}\n\n{quoted}" if quoted else body, cte="quoted-printable")
    return message.as_string()


def _quote_original(thread: EmailThread, *, max_chars: int = 4_000) -> str:
    """The standard Outlook-style quoted original, so context travels with the reply."""
    if not thread.turns:
        return ""
    newest = thread.turns[0]
    header_lines = [
        f"From: {newest.sender}" if newest.sender else "",
        f"Sent: {newest.sent}" if newest.sent else "",
        f"To: {newest.to}" if newest.to else "",
        f"Cc: {newest.cc}" if newest.cc else "",
        f"Subject: {newest.subject or thread.subject}" if (newest.subject or thread.subject) else "",
    ]
    header = "\n".join(line for line in header_lines if line)
    body = newest.body.strip()
    if len(body) > max_chars:
        body = body[:max_chars] + "\n[Original message truncated.]"
    return f"-----Original Message-----\n{header}\n\n{body}"


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(stem: str, extension: str, *, max_length: int = 60) -> str:
    """A download name that survives Windows, macOS, and Linux filesystems."""
    cleaned = _FILENAME_SAFE_RE.sub("-", (stem or "").strip()).strip("-.")
    cleaned = re.sub(r"-{2,}", "-", cleaned)[:max_length].strip("-.")
    return f"{cleaned or 'project-spk'}{extension}"


def build_note_markdown(
    note: dict[str, Any],
    thread: EmailThread,
    analysis: dict[str, Any],
) -> str:
    """A note for the project record, in markdown that pastes cleanly anywhere."""
    received = thread.received_at.astimezone().strftime("%Y-%m-%d %H:%M %Z") if thread.received_at else (
        thread.sent or "unknown"
    )
    lines = [
        f"# {note.get('title') or thread.subject or 'Email note'}",
        "",
        f"- **From:** {thread.sender or 'unknown'}",
        f"- **Received:** {received}",
        f"- **Subject:** {thread.subject or '(none)'}",
        f"- **Category:** {analysis.get('category') or 'uncategorized'}",
        f"- **Priority:** {analysis.get('priority') or 'medium'}",
        "",
        "## Summary",
        "",
        note.get("body") or analysis.get("summary") or "",
    ]

    decisions = note.get("decisions") or []
    if decisions:
        lines += ["", "## Decisions and commitments", ""]
        lines += [f"- {item}" for item in decisions]

    actions = analysis.get("action_items") or []
    if actions:
        lines += ["", "## Action items", ""]
        for item in actions:
            owner = item.get("owner") or "unclear"
            due = item.get("due") or "none stated"
            lines.append(f"- {item.get('action')} — *owner:* {owner}; *due:* {due}")

    deadlines = analysis.get("deadlines") or []
    if deadlines:
        lines += ["", "## Dates called out", ""]
        lines += [f"- {item}" for item in deadlines]

    followups = note.get("followups") or []
    if followups:
        lines += ["", "## Follow up on", ""]
        lines += [f"- {item}" for item in followups]

    lines += [
        "",
        "---",
        "",
        "*Drafted by Project SPK from the email thread. Verify before filing in the project record.*",
    ]
    return "\n".join(lines).strip() + "\n"


def resolve_meeting_window(
    start: datetime,
    *,
    duration_minutes: int | None,
    end: datetime | None = None,
    default_minutes: int = 30,
    max_minutes: int = 8 * 60,
) -> tuple[datetime, datetime]:
    """Settle a sane start/end pair from whatever the model gave us.

    An end that is missing, at or before the start, or implausibly long gets
    replaced with ``start`` plus a bounded duration, so a hallucinated timestamp
    cannot produce a week-long calendar block.
    """
    minutes = duration_minutes if duration_minutes and duration_minutes > 0 else default_minutes
    minutes = min(minutes, max_minutes)
    if end is None or end <= start or (end - start) > timedelta(minutes=max_minutes):
        end = start + timedelta(minutes=minutes)
    return start, end
