# Deploy Project SPK (Railway)

## Prerequisites

- [Railway](https://railway.app) account
- GitHub repo with this project (or deploy via Railway CLI)
- API key: OpenAI (see `.env.example`)

## 1. Push to GitHub

```bash
cd "/Users/willpratt/Library/Mobile Documents/com~apple~CloudDocs/Project SPK"
git init
git add .
git commit -m "Initial Project SPK RAG app"
git remote add origin YOUR_REPO_URL
git push -u origin main
```

## 2. Create Railway project

1. **New Project** → **Deploy from GitHub repo**
2. Select **`prattw/project_SPK`** branch **`main`**
3. **Critical — use Docker, not Railpack**

If build logs say `Railpack could not determine how to build` or snapshot is only ~295 B:

1. Open your **service** → **Settings**
2. **Source** → confirm repo `prattw/project_SPK`, branch `main`
3. **Root Directory** → leave **empty** (or `/`) — NOT a subfolder
4. **Build** → **Builder** → select **Dockerfile**
5. **Dockerfile path** → `Dockerfile`
6. Save → **Redeploy**

This repo includes `railway.toml` and `railway.json` both set to `DOCKERFILE`.

## 3. Environment variables

In Railway → **Variables**, add:

| Variable | Required | Notes |
|----------|----------|--------|
| `OPENAI_API_KEY` | Yes | GPT answers + embeddings |
| `OPENAI_BASE_URL` | | Override API root (Azure/proxy/gateway); blank = OpenAI default |
| `OPENAI_MODEL` | | `gpt-4o-mini` (default) |
| `AUTH_SECRET` | Recommended | Signs 24-hour login tokens; keep stable across deploys |
| `ACCESS_ROSTER` | | Comma-separated allowed emails; defaults to the built-in roster |
| `AUTH_TOKEN_HOURS` | | `24` (default) — re-login interval |
| `APP_API_KEY` | | Optional shared key for scripts/automation (bypasses roster) |
| `EMBEDDING_PROVIDER` | | `openai` (default) |
| `MAX_UPLOAD_MB` | | `300` for large PDFs |
| `CHROMA_PERSIST_DIR` | | `/app/chroma_db` |
| `USAGE_DB_PATH` | | Leave **blank** (uses `{DATA_DIR}/usage.db`, e.g. `/data/files/usage.db`) so metrics survive redeploys. Do **not** point this at ephemeral container storage. |
| `USAGE_ADMIN_EMAILS` | | Comma-separated emails allowed to call `/usage/*` |

Generate a strong `AUTH_SECRET` (e.g. `openssl rand -hex 32`). Without it,
login sessions are invalidated on every restart/redeploy (users just sign in again).

### Access roster

Sign-in is restricted to the roster of approved `@usace.army.mil` emails
(built into `app/config.py`, override with `ACCESS_ROSTER`). Users sign in
with their email and get a signed session token that expires after 24 hours,
after which they must sign in again.

### Usage analytics (retained + Friday report)

The app logs **logins, queries, tokens, latency, uploads, and errors** to SQLite
on the data volume (`{DATA_DIR}/usage.db`). Rows are **never deleted**.

Every **Friday at 5:00 PM Pacific**, the running app automatically writes a
weekly snapshot to:

- SQLite table `weekly_reports`
- `{DATA_DIR}/usage-reports/weekly-YYYY-MM-DD.json`

Week window: previous Friday 5:00 PM PT → this Friday 5:00 PM PT.

Administrators (`USAGE_ADMIN_EMAILS`) can pull reports anytime:

```bash
# All-time summary + retention counts
curl -sS -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/usage/summary"

# Latest completed Friday week (JSON)
curl -sS -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/usage/weekly"

# Human-readable Friday report
curl -sS -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/usage/weekly/text"

# Save a snapshot now + pretty terminal report
python3 scripts/weekly_usage_report.py --save
```

Optional backup: GitHub Actions workflow `.github/workflows/weekly-usage-report.yml`.

The scheduled job signs in via `POST /login` as a usage-admin roster email
(default `william.a.pratt@usace.army.mil`) — **no repository secrets required**.
It uploads the report as a workflow artifact (retained 365 days).

Optional secret overrides: `SPK_URL`, `SPK_LOGIN_EMAIL`, or durable `SPK_TOKEN`
(`APP_API_KEY`). Manual pull:

```bash
python3 scripts/weekly_usage_report.py \
  --login-email william.a.pratt@usace.army.mil --save
```

## 4. Persistent storage (critical)

Without volumes, uploads and the vector index are **lost on redeploy**.

1. Railway → your service → **Volumes**
2. Add volume mount:
   - `/app/chroma_db` — vector index
   - `/app/data` — uploaded files

## 5. Networking

1. **Settings** → generate a **public domain**
2. Open `https://YOUR-APP.up.railway.app/`
3. Sign in with a roster email (session lasts 24 hours)

## 6. Upload timeout

Large PDFs (2000 pages) need a long ingest. If uploads fail:

- Increase Railway/proxy body size limits if available
- Consider raising service memory to **2GB+**

## Local vs production

| | Local | Railway |
|--|-------|---------|
| Start | `./start.sh` | Auto on push |
| URL | http://127.0.0.1:8000 | Public domain |
| Auth | Roster sign-in (24 h sessions) | Roster sign-in + `AUTH_SECRET` |
| Data | `./data`, `./chroma_db` | Mounted volumes |

## 7. Update the Document Library (production)

Use this workflow to add or replace library documents (UFC volumes, ARs, DA PAMs)
**on the live app** without fixing local Python. Requires deploy of the admin
library endpoints and your email in `USAGE_ADMIN_EMAILS`.

### One-time setup

1. Set `MAX_UPLOAD_MB` to **500** or higher on Railway if UFC volumes exceed 300 MB.
2. Merge/deploy the latest code (includes `/admin/library/*` endpoints).
3. Sign in to the app in your browser.

### Get your session token

In the browser DevTools console on the app page:

```javascript
localStorage.getItem("spk_token")
```

Copy the value (without quotes).

### Upload files from your Mac (curl only — no Python)

```bash
cd "/Users/willpratt/Library/Mobile Documents/com~apple~CloudDocs/Project SPK"

export SPK_URL="https://YOUR-APP.up.railway.app"
export SPK_TOKEN="paste-token-here"

chmod +x scripts/upload_library_to_production.sh

./scripts/upload_library_to_production.sh "DOCUMENTS for RAG/New UFC docs for upload JUN26"
./scripts/upload_library_to_production.sh "DOCUMENTS for RAG/ARs"
./scripts/upload_library_to_production.sh "DOCUMENTS for RAG/DA Pams"
```

### Start indexing (replaces old UFC in the search index)

```bash
curl -s -X POST "$SPK_URL/admin/library/ingest" \
  -H "Authorization: Bearer $SPK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"purge_patterns":["UFC"]}'
```

This returns a `job_id`. The server will:

1. Remove existing indexed sources whose filenames contain `UFC`
2. Split any PDF over 1,200 pages into 500-page parts (prevents truncation)
3. Index every file in `library-incoming` into the production ChromaDB volume

### Monitor progress

Live terminal dashboard (recommended):

```bash
chmod +x scripts/watch_library_ingest.sh
./scripts/watch_library_ingest.sh JOB_ID
```

Refreshes every 10 seconds with a progress bar, current file, elapsed time, and ETA.
Press Ctrl+C to stop watching — the ingest keeps running on the server.

Raw JSON (one-shot):

```bash
curl -s "$SPK_URL/jobs/JOB_ID" -H "Authorization: Bearer $SPK_TOKEN"
```

Poll every few minutes. Large UFC compilations can take **hours** — that is normal.
The app stays online; indexing runs in the background on the server.

**Disk space:** Railway’s `/app/data` volume must hold the library PDFs once (not
twice). If ingest fails with `No space left on device`, increase the volume size
in Railway (recommend **20 GB+** for ~700 PDFs including UFC splits), then redeploy
the latest app (moves files instead of copying) and restart ingest.

When `status` is `done`, the documents are **live for all users**.

### Check the upload queue

```bash
curl -s "$SPK_URL/admin/library/incoming" -H "Authorization: Bearer $SPK_TOKEN"
```

## Email Assistant (Outlook)

Opening the **Email Assistant** tab reads the last 72 hours of email, triages every
message, and prepares the work each one implies:

| Output | Format | What the user does with it |
| --- | --- | --- |
| Reply draft | `.eml` | Opens in Outlook as an unsent message, ready to edit and send |
| Note for the record | `.md` | Paste into OneNote, RMS, or the project file |
| Appointment / meeting invite | `.ics` | Double-click to open in Outlook, then save or send |

Plus a digest across the window: what is high priority, what replies are owed, and
every date anyone mentioned.

Project SPK **never sends email and never writes to a calendar.** Every artifact is
a file the user opens, checks, and acts on. It also never invites anyone who was not
already on the thread, and never produces a calendar file for a time it could not
verify.

### Where the mail comes from

| `OUTLOOK_CONNECTOR` | Sweeps unattended? | What it needs |
| --- | --- | --- |
| `manual` (default) | No — the user picks the files | Nothing |
| `local_folder` | **Yes** | A directory the host can read exported `.msg`/`.eml` from |
| `graph` | Yes, once provisioned | Entra ID registration, admin consent, delegated token, security review |

`local_folder` is how the sweep runs autonomously with **no cloud access, no tenant
changes, and no stored credentials**:

```bash
OUTLOOK_CONNECTOR=local_folder
OUTLOOK_LOCAL_FOLDER=/data/mail-inbox
```

An Outlook rule, a scheduled export, or a manual drag fills the folder; Project SPK
only reads it. On Railway, put the folder on the mounted volume so it survives
redeploys. Full setup in
[docs/OUTLOOK_INTEGRATION.md](docs/OUTLOOK_INTEGRATION.md).

With the default `manual` connector the sweep still works — the user selects the
messages in Outlook, drags them to a folder to save them as `.msg`, and uses
**Choose email files**.

> **Read before rollout:** email text is sent to whatever endpoint `OPENAI_API_KEY`
> and `OPENAI_BASE_URL` point at, and a sweep sends 72 hours of it rather than one
> thread the user chose. Email is far more likely than criteria documents to contain
> CUI or PII. To process it on a model you control, see
> [docs/SELF_HOSTED_MODEL.md](docs/SELF_HOSTED_MODEL.md) — it is a configuration
> change, not a code change. Also read the Email Assistant note in
> [SECURITY.md](SECURITY.md#hosting-and-data-caution-read-this).

`GET /email/status` reports the host that actually processes email, and the tab shows
a banner naming it: amber for a commercial API on the public internet, green for a
self-hosted endpoint.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMAIL_ASSISTANT_ENABLED` | `true` | Set `false` to disable the whole email feature |
| `EMAIL_MAX_CHARS` | `60000` | Largest single email thread accepted |
| `EMAIL_SCRUB_PII` | `true` | Redact SSN/EDIPI/DOB/card numbers before the LLM |
| `EMAIL_LIBRARY_TOP_K` | `24` | Retrieval budget when a reply cites the Document Library |
| `EMAIL_SWEEP_ENABLED` | `true` | Set `false` to keep single-email actions but disable the sweep |
| `EMAIL_SWEEP_HOURS` | `72` | How far back a sweep reads |
| `EMAIL_SWEEP_MAX_MESSAGES` | `40` | Messages one sweep analyzes; bounds time and spend |
| `EMAIL_SWEEP_MAX_HOURS` | `336` | Ceiling on a user-supplied window |
| `EMAIL_SWEEP_AUTOSTART` | `true` | Sweep on tab open when the source reads mail unattended |
| `EMAIL_SWEEP_TIMEZONE` | `America/Los_Angeles` | Resolves "Thursday at 10" into a real `.ics` time |
| `EMAIL_SWEEP_DRAFTS` / `_NOTES` / `_INVITES` | `true` | Turn individual outputs off |
| `OUTLOOK_CONNECTOR` | `manual` | `manual`, `local_folder`, or `graph` |
| `OUTLOOK_LOCAL_FOLDER` | _(empty)_ | Directory of exported `.msg`/`.eml` |
| `OUTLOOK_GRAPH_CLOUD` | `gcchigh` | `commercial`, `gcc`, `gcchigh`, or `dod` |
| `OUTLOOK_TENANT_ID` / `OUTLOOK_CLIENT_ID` / `OUTLOOK_CLIENT_SECRET` | _(empty)_ | Entra ID app registration, once provisioned |

Set `EMAIL_SWEEP_TIMEZONE` to the district's timezone, not the server's — it decides
what time lands in a calendar invite.

Budgeting a sweep: at most two model calls per message, so the default cap of 40
messages is 80 calls worst case. Lower `EMAIL_SWEEP_MAX_MESSAGES` or turn off
`EMAIL_SWEEP_DRAFTS` to cut it.

`.msg` parsing needs the `extract-msg` package (already in `requirements.txt`). If it
is missing, `.msg` uploads are skipped with a warning; `.eml` uses the standard
library and always works.

### API

```bash
# What the assistant can do, where the model runs, and what mailbox access needs
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/email/status"

# Sweep the configured source; poll the job for progress and the report
curl -X POST "$SPK_URL/email/sweep" \
  -H "Authorization: Bearer $SPK_TOKEN" -H "Content-Type: application/json" \
  -d '{"hours":72}'
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/jobs/$JOB_ID"

# Sweep a batch the user exported from Outlook
curl -X POST "$SPK_URL/email/sweep/upload" -H "Authorization: Bearer $SPK_TOKEN" \
  -F "files=@msg1.msg" -F "files=@msg2.msg"

# See what a sweep would read, without spending any model calls
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/email/mailbox/messages?hours=72"

# Single email: summarize + triage
curl -X POST "$SPK_URL/email/analyze" \
  -H "Authorization: Bearer $SPK_TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"From: ...\nSubject: ...\n\nbody"}'

# Single email: draft a reply, citing the Document Library
curl -X POST "$SPK_URL/email/draft-reply" \
  -H "Authorization: Bearer $SPK_TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"From: ...","instructions":"Hold the 21-day review period.","tone":"formal","use_library":true}'
```

A sweep report contains message bodies, so `GET /jobs/{id}` returns it only to the
user who started the sweep. Anyone else gets a 404.

### Testing without a key or a mailbox

```bash
python scripts/test_email_sweep.py       # parsing, .ics/.eml/.md output, sweep logic
python scripts/test_email_sweep_api.py   # endpoints, job plumbing, access control
python scripts/run_email_sweep_demo.py   # local server with sample district email on :8010
```

Email actions appear in the Friday weekly report as
`Email agent: N actions (N analyzed, N drafts)`. Subject lines, bodies, and
participants are never stored — only the action type, user, timestamp, and tokens.

## Verify deployment

```bash
curl https://YOUR-APP.up.railway.app/health
```

Expect `"status":"ok"` and `"auth_required":true` (the access roster is active).
