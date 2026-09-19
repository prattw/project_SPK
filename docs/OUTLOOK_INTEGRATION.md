# Outlook email agents — the autonomous sweep, and what direct mailbox access requires

Opening the **Email Assistant** tab reads everything received in the last 72 hours,
works out what each message needs, and prepares the work: a reply draft, a note for
the project record, and an appointment or meeting invite where the thread calls for
one. The user reviews and sends; Project SPK never does.

What it still cannot do on its own is reach into a Microsoft 365 mailbox. That gap
is not a coding problem — it is an access and approval problem, and there is a way
to run the sweep *today* without waiting on it. This document separates the two so
the approval work can proceed in parallel.

## The autonomous sweep

One pass over a window of recent mail. Per message:

| Output | Format | What the user does with it |
| --- | --- | --- |
| Analysis | on screen | Summary, key points, action items with owner and due date, deadlines, open questions, priority, category |
| Reply draft | `.eml` | Opens in Outlook as an unsent message, ready to edit and send |
| Note for the record | `.md` | Paste into OneNote, RMS, or the project file |
| Appointment / meeting invite | `.ics` | Double-click to open in Outlook, then save or send |

Then a digest across the window: how much is high priority, what replies are owed,
and every date anyone mentioned, collected in one place.

The window and the per-sweep message cap are both configurable
(`EMAIL_SWEEP_HOURS`, `EMAIL_SWEEP_MAX_MESSAGES`). Cost is bounded at **two model
calls per message** — one analysis, which also produces the note and the meeting
proposal, plus one draft only when a reply is actually owed. A message needing no
reply costs one call. The digest costs nothing: it is counted in code, so it cannot
disagree with the items it summarizes.

One message failing does not stop the sweep. The failure is recorded against that
message and the rest continue.

### Where the mail comes from

Three sources, behind one interface. The sweep code is identical for all three.

| `OUTLOOK_CONNECTOR` | Sweeps unattended? | What it needs |
| --- | --- | --- |
| `manual` (default) | No — the user picks the files | Nothing |
| `local_folder` | **Yes** | A directory on the Project SPK host that recent mail is exported into |
| `graph` | Yes, once provisioned | Entra ID app registration, admin consent, a delegated token, security review |

#### `local_folder` — autonomy without the cloud

This is the one to reach for first. The whole mailbox problem reduces to a
filesystem read: something outside Project SPK drops recent mail into a directory
as `.msg` or `.eml` files, and the connector reads what is sitting there.

```bash
OUTLOOK_CONNECTOR=local_folder
OUTLOOK_LOCAL_FOLDER=/data/mail-inbox
```

Project SPK holds **no mailbox credentials, makes no network calls to Exchange, and
requires no tenant changes** — so there is nothing for a tenant admin to consent
to. It only ever reads; it never writes, moves, or deletes, so whatever populates
the folder stays in charge of retention.

Ways to populate it, cheapest first:

1. **An Outlook rule with a "run a script" action** that saves incoming mail to the
   folder. Per-user, no admin involvement.
2. **A scheduled PowerShell or Power Automate Desktop export** of the last few days
   from the signed-in Outlook profile.
3. **Manual drag** — select messages in Outlook and drag them into the folder.
   Useful for proving the flow out before automating it.

Messages are filtered by their own `Date`/`Sent` header, falling back to file
modification time. A message with no parseable date is kept rather than silently
skipped, on the principle that analyzing one extra message beats hiding mail the
user expected to see.

#### `manual` — the zero-approval path

With no mail source configured the sweep still works; the user just supplies the
messages. In Outlook, select the last few days, drag them into a folder to save
them as `.msg` files, then use **Choose email files**. Everything downstream is
identical. `POST /email/sweep/upload` accepts the batch.

### What Project SPK will never do

- **Send email.** No code path sends. `Mail.Send` is not requested anywhere, and
  the connector interface exposes draft creation only.
- **Write to a calendar.** Invites are `.ics` files. Nothing reaches a calendar
  until the user opens one and accepts it.
- **Invite someone who was not already on the thread.** Proposed attendees are
  intersected with the thread's own participants, so a hallucinated address cannot
  reach an invite. An empty attendee list stays empty, which is what keeps a
  block-time appointment for yourself from going out as an external meeting
  request.
- **Put a time on the calendar it cannot verify.** A proposed meeting time that is
  unparseable or in the past produces no `.ics` at all. The proposal still appears
  with its agenda and attendees so the user can schedule it themselves.
- **Index email into the document library.** Email text is processed and discarded.

### Single-email actions

Still available under **Work on one email instead**, for when the whole window is
not the point.

| Action | Endpoint | Output |
| --- | --- | --- |
| Run the sweep | `POST /email/sweep` | Job id; poll `GET /jobs/{id}` for progress and the report |
| Sweep uploaded files | `POST /email/sweep/upload` | Same, for a batch of `.msg`/`.eml` files |
| Preview the window | `GET /email/mailbox/messages` | What a sweep would read, with no model calls |
| Summarize & triage | `POST /email/analyze` | Analysis of one pasted thread |
| Draft a reply | `POST /email/draft-reply` | Reply body and subject, in a chosen tone |
| Parse `.msg` | `POST /email/parse-msg` | Extracted thread text and metadata |
| Capability check | `GET /email/status` | What is enabled, where the model runs, and what mailbox access still needs |

Replies can be grounded in the Document Library (`use_library: true`), so a reply
about a submittal review period quotes the controlling ER rather than guessing. If
retrieval fails, the draft is still returned, flagged as ungrounded.

A sweep result contains message bodies, so `GET /jobs/{id}` returns it **only to
the user who started it**. Anyone else gets a 404, not a 403, so job ids cannot be
confirmed by probing.

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

Independent of Graph, and the more consequential of the two questions. It applies
to every path — sweep, paste, and `.msg`. Government email routinely contains CUI,
PII, procurement-sensitive, and attorney-client material.

**The model endpoint is a configuration choice, not a code change.** See
[`SELF_HOSTED_MODEL.md`](SELF_HOSTED_MODEL.md) for how to point Project SPK at a
model that is not on the open internet. The email assistant uses the same LLM
configuration as the rest of the app, so redirecting it moves email processing too.

Because this is the single most important fact about how a deployment handles email,
it is surfaced rather than buried in an environment variable. `GET /email/status`
reports the host that actually processes email, and the Email Assistant tab shows a
banner naming it — amber for a commercial API on the public internet, green for a
self-hosted endpoint.

As defense-in-depth, the app redacts high-confidence identifiers (SSN, EDIPI/DoD
ID, date of birth, payment card numbers) before any text is sent to the model, and
reports what it redacted. Work phone numbers and office addresses are deliberately
kept — they appear in every USACE signature block, and removing them would mangle
content without protecting anything. **This redaction is not a CUI control and does
not by itself make processing controlled content acceptable.**

Note that the sweep changes the *volume*, not the kind, of content processed: 72
hours of mail instead of one thread the user chose. If the endpoint is not
acceptable for one email it is not acceptable for forty, and
`EMAIL_SWEEP_ENABLED=false` turns the sweep off while leaving the single-email
actions available.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMAIL_ASSISTANT_ENABLED` | `true` | Master switch for the whole email feature |
| `EMAIL_MAX_CHARS` | `60000` | Largest single email thread accepted |
| `EMAIL_SCRUB_PII` | `true` | Redact SSN/EDIPI/DOB/card numbers before the LLM |
| `EMAIL_LIBRARY_TOP_K` | `24` | Retrieval budget when a reply cites the Document Library |
| `EMAIL_SWEEP_ENABLED` | `true` | Master switch for the autonomous sweep |
| `EMAIL_SWEEP_HOURS` | `72` | How far back a sweep reads |
| `EMAIL_SWEEP_MAX_MESSAGES` | `40` | Messages one sweep will analyze; bounds time and spend |
| `EMAIL_SWEEP_MAX_HOURS` | `336` | Ceiling on a user-supplied window (14 days) |
| `EMAIL_SWEEP_AUTOSTART` | `true` | Start a sweep when the tab is opened, if the source can read mail unattended |
| `EMAIL_SWEEP_TIMEZONE` | `America/Los_Angeles` | Resolves "Thursday at 10" from email text into a real time |
| `EMAIL_SWEEP_DRAFTS` | `true` | Draft replies for mail that needs one |
| `EMAIL_SWEEP_NOTES` | `true` | Write a note for the record per message |
| `EMAIL_SWEEP_INVITES` | `true` | Build `.ics` appointments and invites |
| `OUTLOOK_CONNECTOR` | `manual` | `manual`, `local_folder`, or `graph` |
| `OUTLOOK_LOCAL_FOLDER` | _(empty)_ | Directory of exported `.msg`/`.eml` for `local_folder` |
| `OUTLOOK_GRAPH_CLOUD` | `gcchigh` | `commercial`, `gcc`, `gcchigh`, or `dod` |
| `OUTLOOK_TENANT_ID` | _(empty)_ | USACE Entra ID tenant |
| `OUTLOOK_CLIENT_ID` | _(empty)_ | App registration client ID |
| `OUTLOOK_CLIENT_SECRET` | _(empty)_ | App registration secret or certificate |

`EMAIL_SWEEP_TIMEZONE` matters more than it looks: it is how "can we talk Thursday
at 10?" becomes an actual `.ics` start time. Set it to the district's timezone, not
the server's.

Setting `OUTLOOK_CONNECTOR=graph` before the above is provisioned does not break
the app: mailbox calls return a clear error listing what is still missing, and the
manual paths keep working. `GET /email/status` reports the same list, and the UI
shows it under "What direct Outlook mailbox access still needs".

## Code layout

| File | Responsibility |
| --- | --- |
| `app/email_messages.py` | `EmailThread`/`EmailTurn` model, Outlook paste parsing, `.msg` and `.eml` parsing, timestamp parsing, PII redaction |
| `app/email_assistant.py` | The agents: `analyze_for_sweep`, `analyze_thread`, `summarize_thread`, `triage_thread`, `draft_reply`; meeting-proposal validation |
| `app/email_artifacts.py` | `.ics` (RFC 5545), `.eml` (RFC 5322, `X-Unsent`), and markdown note generation |
| `app/email_sweep.py` | Windowing, per-message orchestration, the digest |
| `app/outlook_connector.py` | `MailConnector` interface, `ManualConnector`, `LocalFolderConnector`, `GraphMailConnector` (gated) |
| `app/jobs.py` | `email_sweep` background job, progress, owner-scoped results |
| `app/main.py` | `/email/*` endpoints |
| `static/app.js`, `static/index.html` | The Email Assistant tab |

When a delegated token becomes available, the work is confined to
`GraphMailConnector`: implement `recent_threads`, `list_messages`, `get_thread`, and
`create_draft_reply` against the Graph endpoints already resolved in its
constructor. The filter query for the window is written out as a comment on
`list_messages`. Nothing in `email_sweep.py`, `email_assistant.py`, or the UI has to
change to read from a mailbox instead of a folder, because every source produces the
same `EmailThread`.

## Testing

Three suites, none of which need an API key or a mailbox — the model call is stubbed
so the code under test is the real windowing, parsing, validation, and file
generation:

```bash
python scripts/test_email_sweep.py      # parsing, .ics/.eml/.md output, sweep logic
python scripts/test_email_sweep_api.py  # the HTTP endpoints, job plumbing, access control
python scripts/run_email_sweep_demo.py  # a local server with sample USACE email, for UI work
```

`run_email_sweep_demo.py` fills a temp folder with realistic district email
(a submittal review question, a coordination call request, a differing site
conditions claim, an RFI, and a safety bulletin), points `local_folder` at it, and
serves the app on port 8010 so the sweep can be exercised end to end in a browser.

## Usage metrics

Email actions are counted in the usage database (`email_actions` table) and appear
in the Friday 5:00 PM Pacific weekly report as
`Email agent: N actions (N analyzed, N drafts)`, with per-user counts. A sweep is
recorded once, as a `sweep` action, with the token cost of the whole pass.

**Subject lines, bodies, and participants are not stored** — only the action type,
the user, the timestamp, and token counts.
