"""Parse Outlook email into a structured form the email assistant can reason over.

Three input paths work today on a government laptop with no new IT approvals:

* **Paste** — select an email in Outlook, copy, paste into Project SPK. Outlook
  puts ``From:``/``Sent:``/``To:``/``Subject:`` header blocks in the clipboard
  text, and repeats them for each turn of a thread.
* **.msg upload** — drag an email straight out of Outlook. ``.msg`` is an OLE
  compound file; :func:`parse_msg_file` reads it when ``extract-msg`` is
  installed.
* **.eml upload** — RFC 5322 message files, which is what mail rules, exports,
  and non-Outlook clients produce. :func:`parse_eml_file` uses the standard
  library, so it works with no extra packages.

A fourth path (reading the mailbox directly over Microsoft Graph) needs tenant
approvals and lives in :mod:`app.outlook_connector`.

Every parser fills in :attr:`EmailThread.received_at` when the message carries a
usable timestamp. The autonomous sweep in :mod:`app.email_sweep` needs it to
decide what falls inside the last 72 hours.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

# Outlook writes these labels when an email is copied to the clipboard or
# forwarded inline. Localized clients differ; only en-US is handled.
_HEADER_LABELS = ("from", "sent", "to", "cc", "bcc", "subject", "date", "importance", "attachments")
_HEADER_RE = re.compile(
    rf"^\s*(?P<label>{'|'.join(_HEADER_LABELS)})\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(
    r"^\s*(-{2,}\s*(original message|forwarded message|reply above this line)\s*-{2,}|_{10,})\s*$",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

# Conservative, high-confidence identifiers only. Work phone numbers and office
# addresses appear in every USACE signature block and are deliberately kept:
# scrubbing them would mangle useful content without protecting anything.
_SCRUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    (
        "DOD-ID",
        re.compile(
            r"\b(?:EDIPI|DoD\s*ID(?:\s*Number)?|DODID)\b\s*[:#]?\s*(\d{9,10})\b",
            re.IGNORECASE,
        ),
    ),
    ("DOB", re.compile(r"\b(?:DOB|date of birth)\b\s*[:#]?\s*[\d/.-]{6,10}", re.IGNORECASE)),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
)

# Outlook writes "Sent:" in the client's locale. These cover the en-US forms a
# US government install produces; anything else falls back to RFC 5322 parsing.
_SENT_FORMATS: tuple[str, ...] = (
    "%A, %B %d, %Y %I:%M %p",
    "%A, %B %d, %Y %I:%M:%S %p",
    "%B %d, %Y %I:%M %p",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%y %I:%M %p",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)
# Outlook appends a timezone abbreviation to some pastes ("... 9:14 AM PDT").
# AM/PM is excluded so stripping the zone does not also strip the meridiem and
# leave a time the format strings below cannot match.
_TRAILING_TZ_RE = re.compile(r"\s+\(?(?!(?:AM|PM)\b)[A-Z]{2,5}\)?$", re.IGNORECASE)


def parse_email_datetime(value: str) -> datetime | None:
    """Best-effort timestamp from a ``Sent:``/``Date:`` header value.

    Returns an aware UTC datetime, or ``None`` when the text is not a date we
    recognize. Callers treat ``None`` as "unknown age" rather than guessing, so a
    message never lands inside or outside the sweep window by accident.
    """
    text = (value or "").strip()
    if not text:
        return None

    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        bare = _TRAILING_TZ_RE.sub("", text).strip()
        for fmt in _SENT_FORMATS:
            try:
                parsed = datetime.strptime(bare, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None

    # A naive timestamp came from a client that already rendered it in the
    # reader's own timezone, so let astimezone() read it as local time.
    return parsed.astimezone(timezone.utc)


@dataclass
class EmailTurn:
    """One message in a thread, newest first."""

    sender: str = ""
    sent: str = ""
    to: str = ""
    cc: str = ""
    subject: str = ""
    body: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "sender": self.sender,
            "sent": self.sent,
            "to": self.to,
            "cc": self.cc,
            "subject": self.subject,
            "body": self.body,
        }


@dataclass
class EmailThread:
    """A parsed email, plus any quoted earlier turns."""

    subject: str = ""
    sender: str = ""
    sent: str = ""
    to: str = ""
    cc: str = ""
    body: str = ""
    turns: list[EmailTurn] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    source: str = "paste"
    redactions: dict[str, int] = field(default_factory=dict)
    # When the newest message arrived, in UTC. None when the source gave no
    # parseable date — the sweep keeps those rather than silently dropping mail.
    received_at: datetime | None = None
    # RFC 5322 Message-ID, used to thread a drafted reply back to its original.
    message_id: str = ""
    # Opaque handle for the origin (file name, Graph message id). Never a path.
    origin_id: str = ""

    @property
    def sender_address(self) -> str:
        """Just the address from the newest ``From:`` value, if there is one."""
        match = _EMAIL_RE.search(self.sender or "")
        return match.group(0) if match else ""

    @property
    def participants(self) -> list[str]:
        """Every distinct address seen anywhere in the thread."""
        seen: dict[str, None] = {}
        for turn in self.turns:
            for value in (turn.sender, turn.to, turn.cc):
                for address in _EMAIL_RE.findall(value or ""):
                    seen.setdefault(address.lower(), None)
        return list(seen)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "sender": self.sender,
            "sent": self.sent,
            "to": self.to,
            "cc": self.cc,
            "source": self.source,
            "turn_count": len(self.turns),
            "participants": self.participants,
            "attachments": self.attachments,
            "redactions": self.redactions,
            "received_at": self.received_at.isoformat() if self.received_at else "",
            "origin_id": self.origin_id,
        }

    def to_prompt_text(self, *, max_turns: int = 6, max_chars: int = 24_000) -> str:
        """Render the thread for the LLM, newest turn first and oldest truncated."""
        blocks: list[str] = []
        for index, turn in enumerate(self.turns[:max_turns]):
            label = "MOST RECENT MESSAGE" if index == 0 else f"EARLIER MESSAGE {index}"
            header_lines = [
                f"From: {turn.sender}" if turn.sender else "",
                f"Sent: {turn.sent}" if turn.sent else "",
                f"To: {turn.to}" if turn.to else "",
                f"Cc: {turn.cc}" if turn.cc else "",
                f"Subject: {turn.subject}" if turn.subject else "",
            ]
            header = "\n".join(line for line in header_lines if line)
            blocks.append(f"--- {label} ---\n{header}\n\n{turn.body.strip()}")

        text = "\n\n".join(blocks)
        if len(self.turns) > max_turns:
            text += f"\n\n[{len(self.turns) - max_turns} older message(s) in this thread omitted.]"
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[Thread truncated for length.]"
        return text


def scrub_sensitive(text: str) -> tuple[str, dict[str, int]]:
    """Redact high-confidence personal identifiers before text leaves the app.

    Defense-in-depth against incidental PII only. This is NOT a CUI control and
    does not make it safe to process controlled content — see SECURITY.md.
    """
    counts: dict[str, int] = {}
    scrubbed = text
    for label, pattern in _SCRUB_PATTERNS:
        scrubbed, hits = pattern.subn(f"[{label} REDACTED]", scrubbed)
        if hits:
            counts[label] = counts.get(label, 0) + hits
    return scrubbed, counts


def _split_turns(text: str) -> list[dict[str, Any]]:
    """Split pasted text into turns on each Outlook header block."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    index = 0

    while index < len(lines):
        line = lines[index]

        if _SEPARATOR_RE.match(line):
            index += 1
            continue

        match = _HEADER_RE.match(line)
        # A "From:" line always starts a new turn. Any other header label starts
        # the first turn only when nothing has been read yet, so a paste that
        # leads with "Subject:" still yields headers, while a body sentence like
        # "Subject: see attached" mid-message stays body text.
        starts_turn = bool(match) and (
            match.group("label").lower() == "from" or not turns
        )
        if starts_turn:
            current = {"headers": {}, "body": []}
            turns.append(current)

        if match and current is not None and not current["body"]:
            current["headers"][match.group("label").lower()] = match.group("value").strip()
            index += 1
            continue

        if current is None:
            # Text before any header block: treat it as the newest message body.
            current = {"headers": {}, "body": []}
            turns.append(current)
        current["body"].append(line)
        index += 1

    return turns


def parse_pasted_email(text: str, *, scrub: bool = True) -> EmailThread:
    """Parse text copied out of Outlook into a thread."""
    redactions: dict[str, int] = {}
    if scrub:
        text, redactions = scrub_sensitive(text)

    raw_turns = [t for t in _split_turns(text) if t["headers"] or "".join(t["body"]).strip()]
    turns: list[EmailTurn] = []
    for raw in raw_turns:
        headers = raw["headers"]
        turns.append(
            EmailTurn(
                sender=headers.get("from", ""),
                sent=headers.get("sent") or headers.get("date", ""),
                to=headers.get("to", ""),
                cc=headers.get("cc", ""),
                subject=headers.get("subject", ""),
                body="\n".join(raw["body"]).strip(),
            )
        )

    if not turns:
        turns = [EmailTurn(body=text.strip())]

    newest = turns[0]
    subject = next((t.subject for t in turns if t.subject), "")
    return EmailThread(
        subject=subject or newest.subject,
        sender=newest.sender,
        sent=newest.sent,
        to=newest.to,
        cc=newest.cc,
        body=newest.body,
        turns=turns,
        source="paste",
        redactions=redactions,
        received_at=parse_email_datetime(newest.sent),
    )


def msg_support_available() -> bool:
    try:
        import extract_msg  # noqa: F401
    except ImportError:
        return False
    return True


def parse_msg_file(path: str | Path, *, scrub: bool = True) -> EmailThread:
    """Parse an Outlook ``.msg`` file dragged out of Outlook."""
    try:
        import extract_msg
    except ImportError as exc:  # pragma: no cover — depends on install
        raise RuntimeError(
            "Reading .msg files requires the 'extract-msg' package. "
            "Install it with: pip install extract-msg"
        ) from exc

    with extract_msg.Message(str(path)) as message:
        subject = message.subject or ""
        sender = message.sender or ""
        sent = str(message.date or "")
        to = message.to or ""
        cc = message.cc or ""
        body = message.body or ""
        message_id = str(getattr(message, "messageId", "") or "")
        attachments = [
            getattr(a, "longFilename", None) or getattr(a, "shortFilename", None) or "attachment"
            for a in (message.attachments or [])
        ]

    # The .msg body already contains the quoted thread inline, so reuse the
    # paste parser to break it into turns, then restore the real headers.
    thread = parse_pasted_email(body, scrub=scrub)
    header_text = f"{subject}\n{sender}\n{to}\n{cc}"
    redactions = dict(thread.redactions)
    if scrub:
        header_text, header_redactions = scrub_sensitive(header_text)
        for label, count in header_redactions.items():
            redactions[label] = redactions.get(label, 0) + count
        subject, sender, to, cc = (header_text.split("\n") + ["", "", "", ""])[:4]

    if thread.turns:
        newest = thread.turns[0]
        newest.sender = newest.sender or sender
        newest.sent = newest.sent or sent
        newest.to = newest.to or to
        newest.cc = newest.cc or cc
        newest.subject = newest.subject or subject

    thread.subject = subject or thread.subject
    thread.sender = sender or thread.sender
    thread.sent = sent or thread.sent
    thread.to = to or thread.to
    thread.cc = cc or thread.cc
    thread.attachments = attachments
    thread.source = "msg"
    thread.redactions = redactions
    thread.received_at = parse_email_datetime(sent) or thread.received_at
    thread.message_id = message_id
    thread.origin_id = Path(path).name
    return thread


def _eml_body_text(message: Any) -> str:
    """Prefer the plain-text part; fall back to HTML stripped of tags."""
    if message.is_multipart():
        plain = message.get_body(preferencelist=("plain",))
        if plain is not None:
            return plain.get_content()
        html = message.get_body(preferencelist=("html",))
        if html is not None:
            return _strip_html(html.get_content())
        return ""
    content = message.get_content()
    if (message.get_content_type() or "").lower() == "text/html":
        return _strip_html(content)
    return content if isinstance(content, str) else ""


def _strip_html(html: str) -> str:
    """Crude HTML-to-text good enough for an email body.

    Block-level tags become newlines so paragraphs and list items stay separated,
    then remaining tags and entities are removed.
    """
    import html as html_module

    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|blockquote)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def parse_eml_file(path: str | Path, *, scrub: bool = True) -> EmailThread:
    """Parse an RFC 5322 ``.eml`` file using only the standard library."""
    with open(path, "rb") as handle:
        message = BytesParser(policy=policy.default).parse(handle)

    def header(name: str) -> str:
        try:
            return str(message.get(name) or "")
        except Exception:  # noqa: BLE001 — a malformed header must not kill the sweep
            return ""

    subject = header("Subject")
    sender = header("From")
    sent = header("Date")
    to = header("To")
    cc = header("Cc")
    message_id = header("Message-ID")

    attachments: list[str] = []
    if message.is_multipart():
        for part in message.iter_attachments():
            attachments.append(part.get_filename() or "attachment")

    body = _eml_body_text(message)

    # Like .msg, the body carries the quoted thread inline, so reuse the paste
    # parser for turn splitting and then restore the real envelope headers.
    thread = parse_pasted_email(body, scrub=scrub)
    redactions = dict(thread.redactions)
    if scrub:
        header_text, header_redactions = scrub_sensitive(f"{subject}\n{sender}\n{to}\n{cc}")
        for label, count in header_redactions.items():
            redactions[label] = redactions.get(label, 0) + count
        subject, sender, to, cc = (header_text.split("\n") + ["", "", "", ""])[:4]

    if thread.turns:
        newest = thread.turns[0]
        newest.sender = newest.sender or sender
        newest.sent = newest.sent or sent
        newest.to = newest.to or to
        newest.cc = newest.cc or cc
        newest.subject = newest.subject or subject

    thread.subject = subject or thread.subject
    thread.sender = sender or thread.sender
    thread.sent = sent or thread.sent
    thread.to = to or thread.to
    thread.cc = cc or thread.cc
    thread.attachments = attachments
    thread.source = "eml"
    thread.redactions = redactions
    thread.received_at = parse_email_datetime(sent) or thread.received_at
    thread.message_id = message_id
    thread.origin_id = Path(path).name
    return thread


SUPPORTED_MESSAGE_SUFFIXES: tuple[str, ...] = (".msg", ".eml")


def parse_message_file(path: str | Path, *, scrub: bool = True) -> EmailThread:
    """Parse a ``.msg`` or ``.eml`` file, dispatching on the extension."""
    suffix = Path(path).suffix.lower()
    if suffix == ".eml":
        return parse_eml_file(path, scrub=scrub)
    if suffix == ".msg":
        return parse_msg_file(path, scrub=scrub)
    raise ValueError(
        f"Unsupported email file type '{suffix or path}'. Use .msg (from Outlook) or .eml."
    )
