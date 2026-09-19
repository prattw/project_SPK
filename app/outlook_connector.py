"""Mailbox connectors for the email assistant.

Project SPK can obtain email three ways. Two work today; the third needs tenant
approvals that only USACE IT can grant.

=====================  ==========  ============================================
Connector              Status      What it needs
=====================  ==========  ============================================
``manual``             Working     Nothing. The user pastes the email, or drags
                                   ``.msg``/``.eml`` files out of Outlook —
                                   including a multi-select for a whole sweep.
``local_folder``       Working     A directory on the machine running Project
                                   SPK that recent mail is exported into. No
                                   cloud, no tenant changes, no credentials.
``graph``              Gated       An Entra ID (Azure AD) app registration in
                                   the USACE tenant, admin-consented delegated
                                   Mail permissions, and a per-user OAuth token.
=====================  ==========  ============================================

``local_folder`` is what makes the autonomous 72-hour sweep work before any
Microsoft 365 integration exists. Point an Outlook rule, a scheduled export, or
a small local script at a directory; Project SPK reads the files already sitting
there. That keeps mailbox credentials out of the application entirely, which is
also the posture most likely to clear a security review.

**No connector can send email, and none writes to a calendar.** Graph draft
creation is exposed so a reply can be saved into the user's Drafts folder for
them to review and send from Outlook; ``Mail.Send`` is deliberately not
requested anywhere in this codebase.

See ``docs/OUTLOOK_INTEGRATION.md`` for the full provisioning checklist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from app.config import settings
from app.email_messages import (
    SUPPORTED_MESSAGE_SUFFIXES,
    EmailThread,
    parse_message_file,
    parse_pasted_email,
)

# Microsoft Graph hosts differ by cloud. A GCC High or DoD tenant will NOT work
# against the commercial endpoints, and vice versa.
GRAPH_CLOUDS: dict[str, dict[str, str]] = {
    "commercial": {
        "graph": "https://graph.microsoft.com/v1.0",
        "authority": "https://login.microsoftonline.com",
    },
    "gcc": {
        "graph": "https://graph.microsoft.com/v1.0",
        "authority": "https://login.microsoftonline.com",
    },
    "gcchigh": {
        "graph": "https://graph.microsoft.us/v1.0",
        "authority": "https://login.microsoftonline.us",
    },
    "dod": {
        "graph": "https://dod-graph.microsoft.us/v1.0",
        "authority": "https://login.microsoftonline.us",
    },
}

# Read + draft only. Mail.Send is intentionally absent.
REQUIRED_GRAPH_SCOPES: tuple[str, ...] = (
    "offline_access",
    "User.Read",
    "Mail.Read",
    "Mail.ReadWrite",
)


class MailboxUnavailable(RuntimeError):
    """Raised when a mailbox connector is not provisioned for this deployment.

    Carries the remaining setup steps so the UI can tell the user exactly what
    is missing instead of showing a bare failure.
    """

    def __init__(self, message: str, *, requirements: list[str] | None = None) -> None:
        super().__init__(message)
        self.requirements = requirements or []


@dataclass
class MailboxMessageRef:
    """Enough to show a message in a list and fetch it in full."""

    id: str
    subject: str
    sender: str
    received: str
    preview: str
    is_read: bool
    has_attachments: bool


class MailConnector(Protocol):
    """What the email assistant needs from a mailbox, regardless of source."""

    name: str

    def status(self) -> dict[str, Any]:
        """Describe availability and any unmet setup requirements."""

    def list_messages(
        self,
        *,
        user_email: str,
        folder: str = "inbox",
        since: datetime | None = None,
        limit: int = 25,
    ) -> list[MailboxMessageRef]:
        """Most recent messages in a folder, newest first, no older than ``since``."""

    def recent_threads(
        self,
        *,
        user_email: str,
        since: datetime,
        limit: int = 40,
    ) -> list[EmailThread]:
        """Parsed threads received since ``since``, newest first.

        What the autonomous sweep consumes. Separate from
        :meth:`list_messages` so a connector that already has the full message in
        hand does not have to parse it twice.
        """

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        """Fetch one message and its quoted history."""

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        """Save a reply into the user's Drafts folder. Never sends."""


class ManualConnector:
    """The connector that works today: the user supplies the email text.

    There is no mailbox to enumerate, so listing and fetching raise
    :class:`MailboxUnavailable`. :meth:`thread_from_text` is the real entry point.
    """

    name = "manual"

    def status(self) -> dict[str, Any]:
        return {
            "connector": self.name,
            "available": True,
            "can_read_mailbox": False,
            "can_create_drafts": False,
            "can_send": False,
            "description": (
                "Paste an email from Outlook, or upload a .msg file dragged out of Outlook. "
                "Project SPK never connects to your mailbox in this mode."
            ),
            "requirements": [],
        }

    def thread_from_text(self, text: str, *, scrub: bool = True) -> EmailThread:
        return parse_pasted_email(text, scrub=scrub)

    def _no_mailbox(self) -> MailboxUnavailable:
        return MailboxUnavailable(
            "Project SPK is not connected to a mailbox. Select the last few days of email in "
            "Outlook and drag the messages in, or paste a single thread.",
            requirements=[
                "Set OUTLOOK_CONNECTOR=local_folder with OUTLOOK_LOCAL_FOLDER to sweep exported "
                "mail automatically, with no cloud access.",
                "Or provision the Microsoft Graph connector to read the mailbox directly.",
            ],
        )

    def list_messages(
        self,
        *,
        user_email: str,
        folder: str = "inbox",
        since: datetime | None = None,
        limit: int = 25,
    ) -> list[MailboxMessageRef]:
        raise self._no_mailbox()

    def recent_threads(
        self,
        *,
        user_email: str,
        since: datetime,
        limit: int = 40,
    ) -> list[EmailThread]:
        raise self._no_mailbox()

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        raise self._no_mailbox()

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        raise MailboxUnavailable(
            "Project SPK cannot write to your Drafts folder. Download the draft as a .eml and "
            "open it in Outlook instead.",
            requirements=["Enable and provision the Microsoft Graph connector to save drafts."],
        )


class LocalFolderConnector:
    """Read ``.msg``/``.eml`` files from a directory on the Project SPK host.

    The whole mailbox problem reduced to a filesystem read. Something outside
    Project SPK — an Outlook rule with a "run a script" action, a scheduled
    PowerShell export, a Power Automate Desktop flow — drops recent mail into a
    directory, and this connector enumerates it. Project SPK holds no mailbox
    credentials and makes no network calls, so there is nothing for a tenant
    admin to consent to.

    Files are only ever read. Nothing is written, moved, or deleted, so whatever
    populates the directory stays in charge of retention.
    """

    name = "local_folder"

    def __init__(self, folder: str | Path | None = None) -> None:
        configured = str(folder or settings.outlook_local_folder or "").strip()
        self.folder = Path(configured).expanduser() if configured else None

    def missing_requirements(self) -> list[str]:
        if self.folder is None:
            return [
                "OUTLOOK_LOCAL_FOLDER — a directory on the machine running Project SPK that "
                "recent .msg/.eml files are exported into."
            ]
        if not self.folder.exists():
            return [f"The configured folder does not exist: {self.folder}"]
        if not self.folder.is_dir():
            return [f"The configured path is not a directory: {self.folder}"]
        return []

    @property
    def available(self) -> bool:
        return not self.missing_requirements()

    def status(self) -> dict[str, Any]:
        missing = self.missing_requirements()
        return {
            "connector": self.name,
            "available": not missing,
            "can_read_mailbox": not missing,
            "can_create_drafts": False,
            "can_send": False,
            "folder": str(self.folder) if self.folder else "",
            "description": (
                f"Reads exported .msg/.eml files from {self.folder}. Project SPK never connects "
                "to Exchange or Microsoft 365 in this mode, and never modifies the folder."
                if not missing
                else "Configure OUTLOOK_LOCAL_FOLDER to sweep exported mail from a local directory."
            ),
            "requirements": missing,
        }

    def _message_files(self) -> list[Path]:
        if self.folder is None:
            return []
        return [
            path
            for path in self.folder.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_MESSAGE_SUFFIXES
        ]

    def _resolve(self, message_id: str) -> Path:
        """Map an opaque id back to a file, refusing anything outside the folder."""
        if self.folder is None:
            raise MailboxUnavailable(
                "No local mail folder is configured.",
                requirements=self.missing_requirements(),
            )
        name = Path(message_id).name
        if not name or name != message_id:
            raise MailboxUnavailable(f"Unknown message id: {message_id}")
        path = (self.folder / name).resolve()
        if path.parent != self.folder.resolve() or not path.is_file():
            raise MailboxUnavailable(f"Unknown message id: {message_id}")
        return path

    def _received(self, thread: EmailThread, path: Path) -> datetime:
        """The message's own date, falling back to the file's modification time."""
        if thread.received_at:
            return thread.received_at
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    def recent_threads(
        self,
        *,
        user_email: str,
        since: datetime,
        limit: int = 40,
    ) -> list[EmailThread]:
        if not self.available:
            raise MailboxUnavailable(
                "The local mail folder is not usable.",
                requirements=self.missing_requirements(),
            )

        # Skip files whose mtime is well before the window before paying to parse
        # them. A message can be older than its file but never newer, so the
        # margin only has to cover export lag.
        cutoff = since.timestamp() - 86_400
        candidates = sorted(
            (p for p in self._message_files() if p.stat().st_mtime >= cutoff),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        threads: list[tuple[datetime, EmailThread]] = []
        for path in candidates:
            try:
                thread = parse_message_file(path, scrub=settings.email_scrub_pii)
            except Exception:  # noqa: BLE001 — one unreadable file must not stop the sweep
                continue
            received = self._received(thread, path)
            if received < since:
                continue
            thread.received_at = received
            threads.append((received, thread))

        threads.sort(key=lambda item: item[0], reverse=True)
        return [thread for _, thread in threads[:limit]]

    def list_messages(
        self,
        *,
        user_email: str,
        folder: str = "inbox",
        since: datetime | None = None,
        limit: int = 25,
    ) -> list[MailboxMessageRef]:
        window = since or datetime.fromtimestamp(0, tz=timezone.utc)
        return [
            MailboxMessageRef(
                id=thread.origin_id,
                subject=thread.subject,
                sender=thread.sender,
                received=thread.received_at.isoformat() if thread.received_at else "",
                preview=thread.body[:200],
                is_read=True,
                has_attachments=bool(thread.attachments),
            )
            for thread in self.recent_threads(user_email=user_email, since=window, limit=limit)
        ]

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        return parse_message_file(self._resolve(message_id), scrub=settings.email_scrub_pii)

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        raise MailboxUnavailable(
            "Reading a folder does not give Project SPK a mailbox to write drafts into. "
            "Download the draft as a .eml and open it in Outlook.",
            requirements=["Provision the Microsoft Graph connector to save drafts in Outlook."],
        )


class GraphMailConnector:
    """Microsoft Graph connector — scaffolding, not yet operational.

    The HTTP calls against Graph are straightforward; what is missing is the
    delegated access token for the signed-in user. Project SPK authenticates with
    its own email roster and an HMAC session token, not with Entra ID, so there is
    no Azure AD token to exchange for one. Closing that gap requires one of:

    1. **Entra ID SSO for Project SPK.** Sign users in with Entra ID, then use the
       on-behalf-of flow to get a Graph token. Cleanest, and the largest change to
       the existing login.
    2. **A per-user "Connect Outlook" consent flow.** OAuth 2.0 authorization code
       with PKCE, storing each user's refresh token server-side. Smaller change,
       but Project SPK then holds long-lived mailbox credentials, which raises the
       bar on encrypted storage and the security review considerably.
    3. **A local agent on the government laptop.** Read the already-signed-in
       Outlook session locally and send only the extracted text to Project SPK. No
       cloud-stored mailbox credentials at all, and typically the easiest to get
       approved.

    Until one is chosen and provisioned, every method raises
    :class:`MailboxUnavailable` describing what is missing.
    """

    name = "graph"

    def __init__(self) -> None:
        cloud = (settings.outlook_graph_cloud or "commercial").strip().lower()
        self.cloud = cloud if cloud in GRAPH_CLOUDS else "commercial"
        self.graph_base = GRAPH_CLOUDS[self.cloud]["graph"]
        self.authority = f"{GRAPH_CLOUDS[self.cloud]['authority']}/{settings.outlook_tenant_id or 'common'}"

    def missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not settings.outlook_tenant_id:
            missing.append("OUTLOOK_TENANT_ID — the USACE Entra ID tenant ID.")
        if not settings.outlook_client_id:
            missing.append(
                "OUTLOOK_CLIENT_ID — application (client) ID of an Entra ID app registration "
                "in the USACE tenant."
            )
        if not settings.outlook_client_secret:
            missing.append(
                "OUTLOOK_CLIENT_SECRET — client secret or certificate for that app registration."
            )
        missing.append(
            "Tenant admin consent for delegated scopes: " + ", ".join(REQUIRED_GRAPH_SCOPES) + "."
        )
        missing.append(
            "A delegated user access token. Project SPK signs users in with an email roster, "
            "not Entra ID, so no Graph token exists yet — see the module docstring for the "
            "three options and docs/OUTLOOK_INTEGRATION.md."
        )
        missing.append(
            "A security review covering CUI/PII in email content and where it is processed."
        )
        return missing

    def status(self) -> dict[str, Any]:
        missing = self.missing_requirements()
        return {
            "connector": self.name,
            "available": False,
            "can_read_mailbox": False,
            "can_create_drafts": False,
            "can_send": False,
            "cloud": self.cloud,
            "graph_base": self.graph_base,
            "authority": self.authority,
            "scopes": list(REQUIRED_GRAPH_SCOPES),
            "description": (
                "Microsoft Graph mailbox access is scaffolded but not operational. "
                "It needs an Entra ID app registration in the USACE tenant, admin consent, "
                "a delegated user token, and a security review."
            ),
            "requirements": missing,
        }

    def _unavailable(self) -> MailboxUnavailable:
        return MailboxUnavailable(
            "Outlook mailbox access is not provisioned for this deployment yet.",
            requirements=self.missing_requirements(),
        )

    def list_messages(
        self,
        *,
        user_email: str,
        folder: str = "inbox",
        since: datetime | None = None,
        limit: int = 25,
    ) -> list[MailboxMessageRef]:
        # When a token does arrive, this is one call:
        #   GET {graph_base}/me/mailFolders/{folder}/messages
        #       ?$filter=receivedDateTime ge {since:%Y-%m-%dT%H:%M:%SZ}
        #       &$orderby=receivedDateTime desc&$top={limit}
        raise self._unavailable()

    def recent_threads(
        self,
        *,
        user_email: str,
        since: datetime,
        limit: int = 40,
    ) -> list[EmailThread]:
        raise self._unavailable()

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        raise self._unavailable()

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        raise self._unavailable()


CONNECTORS: dict[str, Any] = {
    "manual": ManualConnector,
    "local_folder": LocalFolderConnector,
    "graph": GraphMailConnector,
}


def configured_connector_name() -> str:
    name = (settings.outlook_connector or "manual").strip().lower()
    return name if name in CONNECTORS else "manual"


def get_connector() -> MailConnector:
    """The connector this deployment is configured to use."""
    return CONNECTORS[configured_connector_name()]()


def can_read_mailbox() -> bool:
    """True when a sweep can run without the user supplying files."""
    return bool(get_connector().status().get("can_read_mailbox"))


def connector_status() -> dict[str, Any]:
    """Active connector availability, plus what each inactive one would need."""
    active = get_connector()
    status = active.status()
    status["configured_connector"] = configured_connector_name()
    status["alternatives"] = [
        CONNECTORS[name]().status() for name in CONNECTORS if name != active.name
    ]
    return status
