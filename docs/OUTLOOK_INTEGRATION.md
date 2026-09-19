# Outlook email agents — what works now, and what direct mailbox access requires

Project SPK can read, summarize, triage, and draft replies to Outlook email today.
It cannot yet reach into a user's mailbox on its own, and that gap is not a coding
problem — it is an access and approval problem. This document separates the two so
the approval work can start in parallel with development.

## What works today

No new IT approvals are needed for either of these. Both are deliberate,
per-email user actions, which is the same risk posture as the document uploads
Project SPK already accepts.

### 1. Paste an email

On a government laptop, in Outlook: select the message, `Ctrl+A`, `Ctrl+C`, then
paste into **Email Assistant** in Project SPK. Outlook puts the
`From:` / `Sent:` / `To:` / `Cc:` / `Subject:` header block in the clipboard and
repeats it for each turn of a thread, so the assistant sees who asked what and in
what order.

### 2. Drag a `.msg` file out of Outlook

Drag the message from Outlook onto the desktop (or use **Save As**) and upload the
`.msg` with the **Upload .msg** button. `.msg` is an OLE compound file; the server
parses it with `extract-msg` and pulls out subject, sender, date, recipients,
attachment names, and the quoted thread.

The `.msg` is parsed in a temp directory and deleted. **Email content is never
added to the document search index.**

### What the assistant produces

| Action | Endpoint | Output |
| --- | --- | --- |
| Summarize & triage | `POST /email/analyze` | Summary, key points, action items with owner and due date, deadlines, open questions, priority, category, whether a reply is owed, suggested next step |
| Draft a reply | `POST /email/draft-reply` | Reply body and subject, in a chosen tone, optionally citing the Document Library |
| Parse `.msg` | `POST /email/parse-msg` | Extracted thread text and metadata |
| Capability check | `GET /email/status` | What is enabled, and what mailbox access still needs |

Replies can be grounded in the Document Library (`use_library: true`), so a reply
about a submittal review period quotes the controlling ER rather than guessing. If
retrieval fails, the draft is still returned, flagged as ungrounded.

## What Project SPK will never do

- **Send email.** No code path sends. `Mail.Send` is not requested anywhere, and
  the connector interface exposes draft creation only.
- **Act on a mailbox unattended.** Every action is user-initiated on a specific email.
- **Index email into the document library.** Email text is processed and discarded.

## What direct mailbox access requires

Reading the mailbox itself means Microsoft Graph, and Graph needs the following.
Items 1–4 are USACE IT/CIO actions that cannot be done from this repository.

### 1. Entra ID (Azure AD) app registration in the USACE tenant

Provides `OUTLOOK_TENANT_ID`, `OUTLOOK_CLIENT_ID`, and `OUTLOOK_CLIENT_SECRET`.
A certificate is preferable to a secret for a long-lived registration.

### 2. Admin consent for delegated scopes

Read and draft only:

```
offline_access
User.Read
Mail.Read
Mail.ReadWrite
```

`Mail.Send` is deliberately excluded. `Mail.ReadWrite` is what allows a draft to be
saved into the user's own Drafts folder; the user still presses Send in Outlook.

Request **delegated** permissions, not application permissions. Delegated access is
scoped to the signed-in user's own mailbox. Application permissions (`Mail.Read` as
an app) would grant Project SPK access to *every* mailbox in the tenant, which is
both unnecessary and far harder to get approved.

### 3. The right cloud endpoints

A GCC High or DoD tenant does not work against the commercial endpoints. Set
`OUTLOOK_GRAPH_CLOUD` to match the tenant:

| `OUTLOOK_GRAPH_CLOUD` | Graph endpoint | Login authority |
| --- | --- | --- |
| `commercial` | `graph.microsoft.com` | `login.microsoftonline.com` |
| `gcc` | `graph.microsoft.com` | `login.microsoftonline.com` |
| `gcchigh` (default) | `graph.microsoft.us` | `login.microsoftonline.us` |
| `dod` | `dod-graph.microsoft.us` | `login.microsoftonline.us` |

Confirm which cloud the USACE tenant is in before requesting the registration.

### 4. A delegated user token — the real blocker

**Project SPK signs users in with an email roster and an HMAC session token, not
with Entra ID.** There is therefore no Azure AD token to exchange for a Graph
token. This is the architectural gap, and it has to be closed one of three ways.

#### Option A — Entra ID SSO for Project SPK

Replace the roster login with Entra ID sign-in, then use the on-behalf-of flow to
get a Graph token for the signed-in user.

- Cleanest long-term, and removes the separately maintained roster.
- Largest change: the login screen, `app/auth.py`, and session handling.
- Project SPK stores no mailbox credentials of its own.

#### Option B — A per-user "Connect Outlook" consent flow

Keep the roster login and add an OAuth 2.0 authorization-code + PKCE flow. Each
user consents once; Project SPK stores their refresh token.

- Smaller change to the existing login.
- Project SPK then holds long-lived mailbox credentials, which raises the security
  review bar considerably: encrypted-at-rest token storage, key management,
  rotation, and revocation all become Project SPK's responsibility.

#### Option C — A local agent on the government laptop

A small local component reads the already-signed-in Outlook session (Outlook add-in
or local COM automation) and sends only the extracted text to Project SPK.

- No cloud-stored mailbox credentials at all.
- Usually the easiest to get approved, because the mailbox never leaves the
  endpoint session.
- Needs software-approval and deployment for the local component.

**Recommendation:** pursue Option C for an initial pilot if the goal is to get
something approved and in users' hands, and Option A as the durable answer if
Entra ID SSO is on the roadmap anyway. Option B carries the most security
obligation for the least architectural benefit.

### 5. A security review of where email content is processed

This is independent of Graph and applies to the paste and `.msg` paths already
shipped. Government email routinely contains CUI, PII, procurement-sensitive, and
attorney-client material.

Project SPK currently sends text to a **commercial OpenAI endpoint**
(`OPENAI_API_KEY`, `OPENAI_BASE_URL`). If email content in scope includes CUI, that
destination is very likely not acceptable, and the fix is to point
`OPENAI_BASE_URL` at an accredited deployment — Azure OpenAI in GCC High / DoD at
the required impact level, or a self-hosted model — rather than to change anything
in the email code. The email assistant uses the same LLM configuration as the rest
of the app, so redirecting it moves email processing too.

As defense-in-depth, the app redacts high-confidence identifiers (SSN, EDIPI/DoD
ID, date of birth, payment card numbers) before any text is sent to the model, and
reports what it redacted. Work phone numbers and office addresses are deliberately
kept — they appear in every USACE signature block, and removing them would mangle
content without protecting anything. **This redaction is not a CUI control and does
not by itself make processing controlled content acceptable.**

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMAIL_ASSISTANT_ENABLED` | `true` | Master switch for the paste/`.msg` assistant |
| `EMAIL_MAX_CHARS` | `60000` | Largest email thread accepted |
| `EMAIL_SCRUB_PII` | `true` | Redact SSN/EDIPI/DOB/card numbers before the LLM |
| `EMAIL_LIBRARY_TOP_K` | `24` | Retrieval budget when a reply cites the Document Library |
| `OUTLOOK_CONNECTOR` | `manual` | `manual` or `graph` |
| `OUTLOOK_GRAPH_CLOUD` | `gcchigh` | `commercial`, `gcc`, `gcchigh`, or `dod` |
| `OUTLOOK_TENANT_ID` | _(empty)_ | USACE Entra ID tenant |
| `OUTLOOK_CLIENT_ID` | _(empty)_ | App registration client ID |
| `OUTLOOK_CLIENT_SECRET` | _(empty)_ | App registration secret or certificate |

Setting `OUTLOOK_CONNECTOR=graph` before the above is provisioned does not break
the app: mailbox calls return a clear error listing what is still missing, and the
paste/`.msg` paths keep working. `GET /email/status` reports the same list, and the
UI shows it under "What direct Outlook mailbox access still needs".

## Code layout

| File | Responsibility |
| --- | --- |
| `app/email_messages.py` | `EmailThread`/`EmailTurn` model, Outlook paste parsing, `.msg` parsing, PII redaction |
| `app/email_assistant.py` | The agents: `analyze_thread`, `summarize_thread`, `triage_thread`, `draft_reply` |
| `app/outlook_connector.py` | `MailConnector` interface, `ManualConnector`, `GraphMailConnector` (gated) |
| `app/main.py` | `/email/status`, `/email/analyze`, `/email/draft-reply`, `/email/parse-msg` |
| `static/app.js`, `static/index.html` | The Email Assistant tab |

When a delegated token becomes available, the work is confined to
`GraphMailConnector`: implement `list_messages`, `get_thread`, and
`create_draft_reply` against the Graph endpoints already resolved in its
constructor. Nothing in `email_assistant.py` or the UI has to change to read from a
mailbox instead of a paste, because both produce the same `EmailThread`.

## Usage metrics

Email actions are counted in the usage database (`email_actions` table) and appear
in the Friday 5:00 PM Pacific weekly report as
`Email agent: N actions (N analyzed, N drafts)`, with per-user counts.

**Subject lines, bodies, and participants are not stored** — only the action type,
the user, the timestamp, and token counts.
