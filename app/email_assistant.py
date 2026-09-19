"""Email agents: read, summarize, triage, and draft replies to Outlook email.

Actions, all operating on a parsed :class:`~app.email_messages.EmailThread`:

* :func:`summarize_thread` — what the thread says, decisions, and open questions
* :func:`triage_thread` — priority, category, whether a reply is owed, due dates
* :func:`analyze_for_sweep` — the above, plus a note for the record and a meeting
  proposal, in one call; this is what the autonomous sweep uses
* :func:`draft_reply` — a reply the user edits and sends themselves

Replies can optionally be grounded in the Document Library, so an answer about
submittal review periods quotes the controlling ER instead of guessing.

This module never sends email and never writes to a calendar. Every action
produces text or a file for a human to review; :mod:`app.outlook_connector`
deliberately exposes draft creation but no send.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, tzinfo
from typing import Any

from app.config import settings
from app.email_artifacts import resolve_meeting_window
from app.email_messages import EmailThread
from app.llm import chat_completion

REPLY_TONES: dict[str, str] = {
    "professional": "Professional and courteous — the default register for USACE correspondence.",
    "concise": "As short as possible while still complete. Prefer 2-4 sentences.",
    "formal": "Formal and precise, suitable for contractual or external correspondence.",
    "friendly": "Warm and collegial, for teammates you work with regularly.",
}

DEFAULT_TONE = "professional"

_BASE_PERSONA = """You are an email assistant for a U.S. Army Corps of Engineers (USACE) employee, \
working inside Project SPK (the USACE Policies and Publications AI Assistant).

You understand USACE and federal construction/acquisition context: districts and divisions, project \
delivery teams, submittals and RFIs, modifications and change orders, Engineer Regulations (ER) and \
Manuals (EM), Unified Facilities Criteria (UFC), and the FAR/DFARS/AFARS.

Ground rules:
- Work only from the email text provided. Never invent facts, names, dates, dollar amounts, or commitments.
- If something important is ambiguous or missing, say so rather than guessing.
- Do not make commitments on the user's behalf (schedule, cost, or scope) unless the email text already states them.
- Treat the content as potentially sensitive government correspondence. Do not speculate about individuals."""

_ANALYSIS_FORMAT = """Return ONLY a JSON object, with no markdown fences and no prose around it, shaped exactly like this:

{
  "summary": "2-4 sentence plain-language summary of the thread and where it stands.",
  "key_points": ["Specific factual points, decisions, or positions stated in the thread."],
  "action_items": [
    {"action": "What needs to be done.", "owner": "Who owes it, or 'unclear'.", "due": "Date or timeframe stated in the email, or 'none stated'."}
  ],
  "open_questions": ["Questions the thread raises that are not yet answered."],
  "deadlines": ["Any dates or deadlines explicitly stated in the thread."],
  "priority": "high | medium | low",
  "priority_reason": "One sentence explaining the priority.",
  "category": "Short label, e.g. 'submittal review', 'contract modification', 'RFI', 'scheduling', 'administrative', 'informational'.",
  "reply_needed": true,
  "reply_needed_reason": "One sentence on why a reply is or is not owed.",
  "suggested_next_step": "The single most useful next action for the user."
}

Use empty arrays rather than inventing content. Keep every string under 400 characters."""

# The sweep asks for the note and the meeting proposal in the same call as the
# analysis. All three come from one read of the email, so splitting them into
# separate calls would triple the cost for answers that could disagree.
_SWEEP_FORMAT = """Return ONLY a JSON object, with no markdown fences and no prose around it, shaped exactly like this:

{
  "summary": "2-4 sentence plain-language summary of the thread and where it stands.",
  "key_points": ["Specific factual points, decisions, or positions stated in the thread."],
  "action_items": [
    {"action": "What needs to be done.", "owner": "Who owes it, or 'unclear'.", "due": "Date or timeframe stated in the email, or 'none stated'."}
  ],
  "open_questions": ["Questions the thread raises that are not yet answered."],
  "deadlines": ["Any dates or deadlines explicitly stated in the thread."],
  "priority": "high | medium | low",
  "priority_reason": "One sentence explaining the priority.",
  "category": "Short label, e.g. 'submittal review', 'contract modification', 'RFI', 'scheduling', 'administrative', 'informational'.",
  "reply_needed": true,
  "reply_needed_reason": "One sentence on why a reply is or is not owed.",
  "suggested_next_step": "The single most useful next action for the user.",
  "note": {
    "title": "Short title for a note in the project record.",
    "body": "3-6 sentences a reader who never saw the email could file and understand later.",
    "decisions": ["Decisions or commitments the thread establishes."],
    "followups": ["What the user should follow up on, and roughly when."]
  },
  "meeting": {
    "needed": false,
    "title": "Subject line for the appointment or meeting request.",
    "start": "YYYY-MM-DDTHH:MM local time, or \\"\\" if the email states no specific time.",
    "duration_minutes": 30,
    "location": "Room, address, or 'Microsoft Teams' if the email says so. \\"\\" if unstated.",
    "agenda": ["What the meeting needs to cover, drawn from the thread."],
    "attendees": ["Email addresses from the thread who should be invited."],
    "reason": "One sentence on why this meeting is warranted."
  }
}

Rules for "meeting":
- Set "needed": true only when the thread actually calls for a meeting, site visit, \
call, or a deadline the user should block time for. Ordinary informational mail needs no meeting.
- "start" must be an absolute local datetime. Resolve relative references ("Thursday at 10", \
"tomorrow afternoon") against the current date given below. If the email proposes no time at all, \
set "needed": true only if a meeting is clearly required, and leave "start" empty — the user will pick a time.
- Never invent a time that the email does not support.

Use empty arrays and false rather than inventing content. Keep every string under 600 characters."""


def _library_context(query: str) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve Document Library excerpts a reply can cite.

    Retrieval only — the answer is generated by :func:`draft_reply`'s own prompt,
    and session uploads are excluded so an email reply cites published USACE
    policy rather than someone's draft submittal.
    """
    from app.citations import citations_from_chunks
    from app.context_budget import pack_chunks_for_llm
    from app.rag import get_rag

    rag = get_rag()
    if rag.document_count == 0:
        return "", []

    candidates = rag.retrieve(query, top_k=settings.email_library_top_k, include_library=True)
    library_chunks = [c for c in candidates if (c.get("upload_origin") or "").lower() == "library"]
    if not library_chunks:
        return "", []

    context, selected, _ = pack_chunks_for_llm(
        library_chunks,
        max_focus_chunks_per_source=settings.max_chunks_per_source,
    )
    return context, citations_from_chunks(selected)


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating code fences and surrounding prose."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("The assistant did not return a readable analysis. Try again.")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError("The assistant did not return a readable analysis. Try again.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("The assistant did not return a readable analysis. Try again.")
    return parsed


def _as_str_list(value: Any, *, limit: int = 20) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _as_action_items(value: Any, *, limit: int = 20) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for entry in value[:limit]:
        if isinstance(entry, str):
            items.append({"action": entry.strip(), "owner": "unclear", "due": "none stated"})
        elif isinstance(entry, dict):
            action = str(entry.get("action") or "").strip()
            if not action:
                continue
            items.append(
                {
                    "action": action,
                    "owner": str(entry.get("owner") or "unclear").strip(),
                    "due": str(entry.get("due") or "none stated").strip(),
                }
            )
    return items


def _normalize_analysis(parsed: dict[str, Any]) -> dict[str, Any]:
    priority = str(parsed.get("priority") or "medium").strip().lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"

    reply_needed = parsed.get("reply_needed")
    if isinstance(reply_needed, str):
        reply_needed = reply_needed.strip().lower() in {"true", "yes", "y"}

    return {
        "summary": str(parsed.get("summary") or "").strip(),
        "key_points": _as_str_list(parsed.get("key_points")),
        "action_items": _as_action_items(parsed.get("action_items")),
        "open_questions": _as_str_list(parsed.get("open_questions")),
        "deadlines": _as_str_list(parsed.get("deadlines")),
        "priority": priority,
        "priority_reason": str(parsed.get("priority_reason") or "").strip(),
        "category": str(parsed.get("category") or "").strip(),
        "reply_needed": bool(reply_needed),
        "reply_needed_reason": str(parsed.get("reply_needed_reason") or "").strip(),
        "suggested_next_step": str(parsed.get("suggested_next_step") or "").strip(),
    }


def _normalize_note(value: Any, analysis: dict[str, Any], thread: EmailThread) -> dict[str, Any]:
    data = value if isinstance(value, dict) else {}
    title = str(data.get("title") or "").strip() or (thread.subject or "Email note")
    body = str(data.get("body") or "").strip() or analysis.get("summary", "")
    return {
        "title": title[:200],
        "body": body,
        "decisions": _as_str_list(data.get("decisions"), limit=12),
        "followups": _as_str_list(data.get("followups"), limit=12),
    }


def _local_datetime(value: Any, *, tz: tzinfo) -> datetime | None:
    """Parse a model-supplied local datetime string into an aware datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if "T" not in text and " " in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed


def _normalize_meeting(
    value: Any,
    thread: EmailThread,
    *,
    tz: tzinfo,
    now: datetime,
) -> dict[str, Any] | None:
    """Validate a meeting proposal, or return None when there is nothing to schedule.

    A proposal with no usable time still comes back, flagged ``time_known: False``,
    so the UI can offer the agenda and attendee list for the user to schedule
    themselves. Resolved times that land in the past are treated as unusable
    rather than written into a calendar file.
    """
    data = value if isinstance(value, dict) else {}
    needed = data.get("needed")
    if isinstance(needed, str):
        needed = needed.strip().lower() in {"true", "yes", "y"}
    if not needed:
        return None

    start = _local_datetime(data.get("start"), tz=tz)
    end = _local_datetime(data.get("end"), tz=tz)
    duration = data.get("duration_minutes")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    time_known = start is not None and start >= now
    if start is not None and time_known:
        start, end = resolve_meeting_window(start, duration_minutes=duration, end=end)
    else:
        start, end = None, None

    attendees = [
        address.lower()
        for address in _as_str_list(data.get("attendees"), limit=25)
        if "@" in address
    ]
    if not attendees:
        attendees = thread.participants[:25]

    return {
        "title": (str(data.get("title") or "").strip() or thread.subject or "Meeting")[:200],
        "start": start,
        "end": end,
        "time_known": time_known,
        "duration_minutes": duration or 30,
        "location": str(data.get("location") or "").strip()[:200],
        "agenda": _as_str_list(data.get("agenda"), limit=12),
        "attendees": attendees,
        "reason": str(data.get("reason") or "").strip(),
        "description": str(data.get("reason") or "").strip(),
    }


def analyze_for_sweep(
    thread: EmailThread,
    *,
    user_email: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Analysis plus a note and a meeting proposal, in a single LLM call.

    The current date is supplied so the model can turn "Thursday at 10" into an
    absolute time; :func:`_normalize_meeting` then throws out anything it cannot
    verify, because a wrong calendar entry is worse than none.
    """
    tz = settings.sweep_tzinfo
    now = (now or datetime.now(tz)).astimezone(tz)
    viewer = f"\n\nThe user reading this email is {user_email}." if user_email else ""
    clock = (
        f"\n\nThe current date and time is {now.strftime('%A, %B %d, %Y at %I:%M %p %Z')}. "
        "Resolve every relative date or time against it."
    )
    messages = [
        {"role": "system", "content": f"{_BASE_PERSONA}{viewer}{clock}\n\n{_SWEEP_FORMAT}"},
        {
            "role": "user",
            "content": (
                f"Analyze this Outlook email thread. Produce the analysis, a note for the "
                f"project record, and a meeting proposal if one is warranted.\n\n"
                f"Subject: {thread.subject or '(none)'}\n"
                f"Received: {thread.sent or 'unknown'}\n\n"
                f"{thread.to_prompt_text()}"
            ),
        },
    ]
    raw = chat_completion(messages, temperature=0.2)
    parsed = _parse_json_response(raw)
    analysis = _normalize_analysis(parsed)
    return {
        "analysis": analysis,
        "note": _normalize_note(parsed.get("note"), analysis, thread),
        "meeting": _normalize_meeting(parsed.get("meeting"), thread, tz=tz, now=now),
    }


def analyze_thread(thread: EmailThread, *, user_email: str | None = None) -> dict[str, Any]:
    """Summarize and triage a thread in one LLM call.

    Summary and triage come from the same read of the email, so they are produced
    together rather than in two calls that could disagree with each other.
    """
    viewer = f"\n\nThe user reading this email is {user_email}." if user_email else ""
    messages = [
        {"role": "system", "content": f"{_BASE_PERSONA}{viewer}\n\n{_ANALYSIS_FORMAT}"},
        {
            "role": "user",
            "content": (
                f"Analyze this Outlook email thread.\n\n"
                f"Subject: {thread.subject or '(none)'}\n\n"
                f"{thread.to_prompt_text()}"
            ),
        },
    ]
    raw = chat_completion(messages, temperature=0.2)
    return _normalize_analysis(_parse_json_response(raw))


def summarize_thread(thread: EmailThread, *, user_email: str | None = None) -> dict[str, Any]:
    """Summary-focused view of :func:`analyze_thread`."""
    analysis = analyze_thread(thread, user_email=user_email)
    return {
        key: analysis[key]
        for key in ("summary", "key_points", "action_items", "open_questions", "deadlines")
    }


def triage_thread(thread: EmailThread, *, user_email: str | None = None) -> dict[str, Any]:
    """Triage-focused view of :func:`analyze_thread`."""
    analysis = analyze_thread(thread, user_email=user_email)
    return {
        key: analysis[key]
        for key in (
            "priority",
            "priority_reason",
            "category",
            "reply_needed",
            "reply_needed_reason",
            "suggested_next_step",
        )
    }


def draft_reply(
    thread: EmailThread,
    *,
    instructions: str = "",
    tone: str = DEFAULT_TONE,
    use_library: bool = False,
    user_email: str | None = None,
    user_name: str = "",
) -> dict[str, Any]:
    """Draft a reply for the user to review, edit, and send themselves."""
    tone_key = (tone or DEFAULT_TONE).strip().lower()
    tone_hint = REPLY_TONES.get(tone_key, REPLY_TONES[DEFAULT_TONE])

    context = ""
    citations: list[dict[str, Any]] = []
    library_error = ""
    if use_library:
        retrieval_query = " ".join(
            part for part in (thread.subject, instructions, thread.body[:1500]) if part
        )
        try:
            context, citations = _library_context(retrieval_query)
        except Exception as exc:  # noqa: BLE001 — an ungrounded draft beats no draft
            library_error = f"Document Library lookup failed ({exc}); the draft is not grounded."

    grounding = ""
    if context:
        grounding = f"""

USACE Document Library excerpts you may cite (use ONLY these for policy statements):

{context}

When you rely on an excerpt, cite it inline as [Document Number, Page N]. If the excerpts do not \
answer a policy question raised in the email, say what still needs to be confirmed instead of \
citing something that does not apply."""

    signature = f"\n\nSign the draft as {user_name}." if user_name else ""
    viewer = f"\n\nYou are writing as {user_email}." if user_email else ""

    system = f"""{_BASE_PERSONA}{viewer}

You are drafting a REPLY for the user to review and send themselves. You are not sending anything.

Tone: {tone_hint}

Output rules:
- Return only the reply body text. No subject line, no "Here is a draft:" preamble, no markdown fences.
- Open with an appropriate greeting and close with a sign-off.{signature}
- Address every question and action directed at the user in the most recent message.
- Where information is genuinely missing, write a bracketed placeholder like [confirm submittal date] \
rather than inventing a fact. Keep placeholders to a minimum.
- Do not commit to schedule, cost, or scope beyond what the thread already establishes.{grounding}"""

    user_instructions = (
        f"\n\nThe user's instructions for this reply: {instructions.strip()}"
        if instructions.strip()
        else "\n\nThe user gave no specific instructions — write the reply the thread calls for."
    )

    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": (
                f"Draft a reply to this Outlook email thread.\n\n"
                f"Subject: {thread.subject or '(none)'}\n\n"
                f"{thread.to_prompt_text()}{user_instructions}"
            ),
        },
    ]

    body = chat_completion(messages, temperature=0.4).strip()
    subject = thread.subject or ""
    if subject and not re.match(r"^\s*re\s*:", subject, re.IGNORECASE):
        subject = f"RE: {subject}"

    return {
        "subject": subject,
        "body": body,
        "tone": tone_key,
        "used_library": bool(context),
        "citations": citations,
        "library_error": library_error,
    }
