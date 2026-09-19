"""Mailbox connectors for the email assistant.

Project SPK can obtain an email three ways. Two work today; the third needs
tenant approvals that only USACE IT can grant.

=====================  ==========  ============================================
Connector              Status      What it needs
=====================  ==========  ============================================
``manual``             Working     Nothing. The user pastes the email, or drags
                                   a ``.msg`` file out of Outlook.
``graph``              Gated       An Entra ID (Azure AD) app registration in
                                   the USACE tenant, admin-consented delegated
                                   Mail permissions, and a per-user OAuth token.
=====================  ==========  ============================================

**No connector can send email.** Graph draft creation is exposed so a reply can
be saved into the user's Drafts folder for them to review and send from Outlook;
``Mail.Send`` is deliberately not requested anywhere in this codebase.

See ``docs/OUTLOOK_INTEGRATION.md`` for the full provisioning checklist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.config import settings
from app.email_messages import EmailThread, parse_pasted_email

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

    def list_messages(self, *, user_email: str, folder: str = "inbox", limit: int = 25) -> list[MailboxMessageRef]:
        """Most recent messages in a folder, newest first."""

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

    def list_messages(self, *, user_email: str, folder: str = "inbox", limit: int = 25) -> list[MailboxMessageRef]:
        raise MailboxUnavailable(
            "Project SPK is not connected to your mailbox. Paste an email or upload a .msg file instead.",
            requirements=["Enable and provision the Microsoft Graph connector to browse a mailbox."],
        )

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        raise MailboxUnavailable(
            "Project SPK is not connected to your mailbox. Paste an email or upload a .msg file instead.",
            requirements=["Enable and provision the Microsoft Graph connector to browse a mailbox."],
        )

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        raise MailboxUnavailable(
            "Project SPK cannot write to your Drafts folder. Copy the draft into Outlook instead.",
            requirements=["Enable and provision the Microsoft Graph connector to save drafts."],
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

    def list_messages(self, *, user_email: str, folder: str = "inbox", limit: int = 25) -> list[MailboxMessageRef]:
        raise self._unavailable()

    def get_thread(self, *, user_email: str, message_id: str) -> EmailThread:
        raise self._unavailable()

    def create_draft_reply(self, *, user_email: str, message_id: str, body: str) -> dict[str, Any]:
        raise self._unavailable()


def get_connector() -> MailConnector:
    """The connector this deployment is configured to use."""
    if (settings.outlook_connector or "manual").strip().lower() == "graph":
        return GraphMailConnector()
    return ManualConnector()


def connector_status() -> dict[str, Any]:
    """Connector availability plus what the other connector would need."""
    active = get_connector()
    status = active.status()
    status["configured_connector"] = (settings.outlook_connector or "manual").strip().lower()
    if active.name != "graph":
        status["graph"] = GraphMailConnector().status()
    return status
