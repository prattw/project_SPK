"""Autonomous sweep: read a window of recent email and prepare the work it implies.

One pass over everything received in the last N hours (72 by default). For each
message the sweep produces, without being asked:

* an analysis — summary, key points, action items, deadlines, priority, category
* a note for the project record, as markdown
* a reply draft, as an ``.eml`` Outlook opens in compose mode, when a reply is owed
* an appointment or meeting request, as an ``.ics``, when the thread calls for one

Then a digest across the whole window: what is urgent, what is owed, what is due.

Cost and safety are both bounded deliberately:

* At most two model calls per message — one analysis (which also yields the note
  and the meeting proposal) and one reply draft. A message needing no reply costs one.
* ``email_sweep_max_messages`` caps how many messages a single sweep will touch.
* One message failing is recorded against that message and the sweep continues.
* **Nothing is sent and nothing is written to a calendar.** Every artifact is file
  content handed back for the user to open, edit, and act on themselves.

Where the messages come from is :mod:`app.outlook_connector`'s problem. This
module takes parsed threads, so it works identically for a folder of exported
mail, a batch the user dragged out of Outlook, or Microsoft Graph later on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.email_artifacts import (
    build_ics,
    build_note_markdown,
    build_reply_eml,
    safe_filename,
)
from app.email_assistant import DEFAULT_TONE, analyze_for_sweep, draft_reply
from app.email_messages import EmailThread

# phase, done, total, detail
ProgressCallback = Callable[[str, int, int, str], None]

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}

# Artifacts are built in whatever order is cheapest, but presented in the order
# the user is most likely to act: answer the email, put it on the calendar, file
# the note.
_ARTIFACT_ORDER = {"reply": 0, "invite": 1, "note": 2, "invite_error": 3}


@dataclass
class SweepItem:
    """Everything the sweep produced for one message."""

    thread: EmailThread
    analysis: dict[str, Any] = field(default_factory=dict)
    note: dict[str, Any] | None = None
    meeting: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    artifacts: list[dict[str, str]] = field(default_factory=list)
    error: str = ""

    @property
    def priority(self) -> str:
        return str(self.analysis.get("priority") or "medium")

    def to_dict(self) -> dict[str, Any]:
        meeting = None
        if self.meeting:
            meeting = {
                **{k: v for k, v in self.meeting.items() if k not in ("start", "end")},
                "start": self.meeting["start"].isoformat() if self.meeting.get("start") else "",
                "end": self.meeting["end"].isoformat() if self.meeting.get("end") else "",
            }
        return {
            "email": self.thread.as_dict(),
            "analysis": self.analysis,
            "note": self.note,
            "meeting": meeting,
            "draft": self.draft,
            "artifacts": sorted(
                self.artifacts, key=lambda a: _ARTIFACT_ORDER.get(a.get("kind", ""), 9)
            ),
            "error": self.error,
        }


@dataclass
class SweepReport:
    """The whole sweep: per-message results plus a digest across the window."""

    window_hours: int
    since: datetime
    until: datetime
    source: str
    messages_found: int = 0
    messages_analyzed: int = 0
    messages_failed: int = 0
    messages_skipped: int = 0
    truncated: bool = False
    items: list[SweepItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def sorted_items(self) -> list[SweepItem]:
        """Most important first: high priority, then newest."""

        def key(item: SweepItem) -> tuple[int, float]:
            received = item.thread.received_at
            age = -received.timestamp() if received else 0.0
            return (_PRIORITY_ORDER.get(item.priority, 1), age)

        return sorted(self.items, key=key)

    def digest(self) -> dict[str, Any]:
        """A window-level summary, computed from the items rather than by the model.

        Counting is something code does correctly and cheaply, so the digest costs
        no extra model call and cannot disagree with the items it summarizes.
        """
        counts = {"high": 0, "medium": 0, "low": 0}
        deadlines: list[dict[str, str]] = []
        actions: list[dict[str, str]] = []
        needs_reply: list[dict[str, str]] = []

        for item in self.sorted_items():
            if item.error:
                continue
            counts[item.priority] = counts.get(item.priority, 0) + 1
            subject = item.thread.subject or "(no subject)"
            for deadline in item.analysis.get("deadlines") or []:
                deadlines.append({"subject": subject, "deadline": deadline})
            for action in item.analysis.get("action_items") or []:
                actions.append(
                    {
                        "subject": subject,
                        "action": action.get("action", ""),
                        "owner": action.get("owner", "unclear"),
                        "due": action.get("due", "none stated"),
                    }
                )
            if item.analysis.get("reply_needed"):
                needs_reply.append(
                    {
                        "subject": subject,
                        "sender": item.thread.sender,
                        "reason": item.analysis.get("reply_needed_reason", ""),
                    }
                )

        return {
            "priority_counts": counts,
            "replies_drafted": sum(1 for item in self.items if item.draft),
            "notes_written": sum(1 for item in self.items if item.note),
            "meetings_proposed": sum(1 for item in self.items if item.meeting),
            "invites_built": sum(
                1 for item in self.items if any(a["kind"] == "invite" for a in item.artifacts)
            ),
            "needs_reply": needs_reply[:25],
            "deadlines": deadlines[:25],
            "action_items": actions[:40],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.window_hours,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "source": self.source,
            "messages_found": self.messages_found,
            "messages_analyzed": self.messages_analyzed,
            "messages_failed": self.messages_failed,
            "messages_skipped": self.messages_skipped,
            "truncated": self.truncated,
            "warnings": self.warnings,
            "digest": self.digest(),
            "items": [item.to_dict() for item in self.sorted_items()],
        }


def window_bounds(hours: int | None = None, *, now: datetime | None = None) -> tuple[datetime, datetime, int]:
    """Clamp a requested window and return ``(since, until, hours)`` in UTC."""
    requested = hours or settings.email_sweep_hours
    clamped = max(1, min(int(requested), settings.email_sweep_max_hours))
    until = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return until - timedelta(hours=clamped), until, clamped


def select_threads(
    threads: Sequence[EmailThread],
    *,
    since: datetime,
    limit: int | None = None,
) -> tuple[list[EmailThread], int]:
    """Keep threads inside the window, newest first, and report how many were dropped.

    A thread with no parseable date is kept: the sweep would rather analyze one
    extra message than silently skip mail the user expected to see.
    """
    cap = limit or settings.email_sweep_max_messages
    in_window = [t for t in threads if t.received_at is None or t.received_at >= since]
    skipped = len(threads) - len(in_window)
    in_window.sort(
        key=lambda t: t.received_at or datetime.now(timezone.utc),
        reverse=True,
    )
    return in_window[:cap], skipped


def run_sweep(
    threads: Sequence[EmailThread],
    *,
    user_email: str | None = None,
    user_name: str = "",
    source: str = "manual",
    hours: int | None = None,
    now: datetime | None = None,
    progress: ProgressCallback | None = None,
    draft_replies: bool | None = None,
    write_notes: bool | None = None,
    build_invites: bool | None = None,
    tone: str = DEFAULT_TONE,
    use_library: bool = False,
    messages_found: int | None = None,
) -> SweepReport:
    """Analyze a window of threads and build the artifacts each one calls for."""
    since, until, window_hours = window_bounds(hours, now=now)
    selected, skipped = select_threads(threads, since=since)

    report = SweepReport(
        window_hours=window_hours,
        since=since,
        until=until,
        source=source,
        messages_found=messages_found if messages_found is not None else len(threads),
        messages_skipped=skipped,
        truncated=len(threads) - skipped > len(selected),
    )
    if report.truncated:
        report.warnings.append(
            f"Analyzed the {len(selected)} most recent message(s); "
            f"{len(threads) - skipped - len(selected)} more fell inside the window. "
            "Raise EMAIL_SWEEP_MAX_MESSAGES or narrow the window to cover them."
        )

    want_drafts = settings.email_sweep_drafts if draft_replies is None else draft_replies
    want_notes = settings.email_sweep_notes if write_notes is None else write_notes
    want_invites = settings.email_sweep_invites if build_invites is None else build_invites

    total = len(selected)
    for index, thread in enumerate(selected):
        subject = thread.subject or "(no subject)"
        if progress:
            progress("analyze", index, total, subject)

        item = SweepItem(thread=thread)
        report.items.append(item)
        try:
            result = analyze_for_sweep(thread, user_email=user_email, now=now)
        except Exception as exc:  # noqa: BLE001 — one bad message must not end the sweep
            item.error = f"Could not analyze this message: {exc}"
            report.messages_failed += 1
            continue

        item.analysis = result["analysis"]
        report.messages_analyzed += 1

        if want_notes:
            item.note = result["note"]
            item.artifacts.append(
                {
                    "kind": "note",
                    "label": "Note for the record",
                    "filename": safe_filename(f"note-{subject}", ".md"),
                    "mime": "text/markdown",
                    "content": build_note_markdown(item.note, thread, item.analysis),
                }
            )

        meeting = result["meeting"]
        if meeting and want_invites:
            item.meeting = meeting
            _attach_invite(item, meeting, thread, user_email=user_email, now=now)

        if want_drafts and item.analysis.get("reply_needed"):
            if progress:
                progress("draft", index, total, subject)
            _attach_draft(
                item,
                thread,
                user_email=user_email,
                user_name=user_name,
                tone=tone,
                use_library=use_library,
                now=now,
            )

    if progress:
        progress("done", total, total, "")
    return report


def _attach_invite(
    item: SweepItem,
    meeting: dict[str, Any],
    thread: EmailThread,
    *,
    user_email: str | None,
    now: datetime | None,
) -> None:
    """Build the .ics, but only when the proposal has a time we trust.

    A meeting the email implies without naming a time still surfaces in the UI —
    with its agenda and attendee list — so the user can schedule it. It just does
    not become a calendar file with a made-up start.
    """
    if not meeting.get("time_known"):
        return
    try:
        content = build_ics(
            meeting,
            organizer=(user_email or "").lower(),
            attendees=meeting.get("attendees") or [],
            now=now,
        )
    except ValueError as exc:
        item.artifacts.append(
            {
                "kind": "invite_error",
                "label": "Calendar invite unavailable",
                "filename": "",
                "mime": "",
                "content": str(exc),
            }
        )
        return

    kind_label = "Meeting invite" if meeting.get("attendees") else "Appointment"
    item.artifacts.append(
        {
            "kind": "invite",
            "label": kind_label,
            "filename": safe_filename(meeting.get("title") or "meeting", ".ics"),
            "mime": "text/calendar",
            "content": content,
        }
    )


def _attach_draft(
    item: SweepItem,
    thread: EmailThread,
    *,
    user_email: str | None,
    user_name: str,
    tone: str,
    use_library: bool,
    now: datetime | None,
) -> None:
    try:
        draft = draft_reply(
            thread,
            tone=tone,
            use_library=use_library,
            user_email=user_email,
            user_name=user_name,
        )
    except Exception as exc:  # noqa: BLE001 — keep the analysis even if drafting fails
        item.error = f"Analyzed, but could not draft a reply: {exc}"
        return

    item.draft = draft
    item.artifacts.append(
        {
            "kind": "reply",
            "label": "Reply draft",
            "filename": safe_filename(f"reply-{thread.subject or 'email'}", ".eml"),
            "mime": "message/rfc822",
            "content": build_reply_eml(
                draft,
                thread,
                from_address=(user_email or "").lower(),
                now=now,
            ),
        }
    )
