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

## Document Library index pages

The Document Library tab is split into five indexes. Each is deep-linkable, so
you can bookmark or share a single index:

| Index | URL | Contents |
| --- | --- | --- |
| All Documents | `/#library` | Every indexed library document (build-time list) |
| Government Engineering | `/#library/engineering` | ER, EM, EP, EC, ETL, ECB, UFC, TSPWG, Tri-Service, TM, MIL-STD, space planning, OM, PN, HQ policy memos, and engineering-series AR/PAM |
| Government Contracting & Law | `/#library/contracting-law` | FAR, DFARS, AFARS, PGI, United States Code, UAI/UDG, IDaC, and legal/contracting/administrative AR/PAM |
| Discipline Knowledge | `/#library/discipline-knowledge` | Textbooks and professional references kept for study — filed here by hand, never by inference |
| Miscellaneous Documents | `/#library/miscellaneous` | Everything that matched no publication convention and was not filed on a page |

The four subject indexes render live from the search index, so documents appear
as soon as they are ingested — no `build_library_html.py` rebuild required.

The first two hold the publications people consult to do the work. The third,
titled the William Held Pratt Memorial Engineering & Science Library, is a reading
collection kept for study rather than daily reference; nothing routes a document
there, because no filename can say that a book was chosen for the collection.

That leaves documents nothing could be inferred about, and Miscellaneous Documents
is where they go. Each of the other three is defined by what it holds, so none can
absorb the unclassifiable without becoming a poorer description of itself. A page
of its own keeps those documents browsable and searchable while saying plainly
that nobody has filed them.

**On first deploy** every document currently sitting on an index because it fell
through to a default moves to Miscellaneous Documents. Nothing is deleted and
nothing leaves the search index — expect the Discipline Knowledge count to drop to
whatever has actually been filed there, which is nothing until you file something.

### API

```bash
# Document counts per index
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/library/groups"

# One index, with download URLs
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/library/groups/engineering"

# /files also accepts group + origin filters
curl -H "Authorization: Bearer $SPK_TOKEN" "$SPK_URL/files?group=contracting-law&origin=library"
```

### Filing a whole folder on one index page

Routing by filename only works for documents that follow a publication naming
convention. A folder of textbooks and references has no convention to read, so
tell the upload where the documents belong instead of hoping inference gets it
right:

```bash
export SPK_URL="https://YOUR-APP.up.railway.app"
export SPK_TOKEN="paste-token-here"

python3 scripts/zip_upload_library.py "$HOME/Documents/Master Library" \
  --group miscellaneous --ingest
```

On Windows, PowerShell sets variables differently and the interpreter is `python`,
not `python3`. Quote the folder — these paths contain spaces:

```powershell
$env:SPK_URL = "https://YOUR-APP.up.railway.app"
$env:SPK_TOKEN = "paste-token-here"

python scripts\zip_upload_library.py "C:\Users\CYRUS\Documents\Master Library" --group miscellaneous --ingest
```

Add `--dry-run` to either form to list what would be uploaded without sending
anything. `--ingest` indexes the batch and follows the job to the end, printing
progress and any per-file failures; leave it off to upload now and index later.

Naming a page even for the leftovers is worth doing. Without `--group`, any file
whose name happens to mention a publication — `AR 420-1 Class Handout.pdf` — is
routed to that publication's index and shown as the regulation it merely quotes.
Filing the batch deliberately stops that at the door.

The group is recorded against each queued file, so the ingest that follows needs
no extra flag:

```bash
curl -s -X POST "$SPK_URL/admin/library/ingest" -H "Authorization: Bearer $SPK_TOKEN"
```

Because the assignment lives in a manifest inside `library-incoming` rather than
in the ingest request, it survives an upload split across several sessions and an
ingest that crashes and resumes — including `scripts/robust_library_ingest.py`,
which needs no changes to preserve it. An oversized PDF that gets split into
page-range parts passes its assignment down to every part.

`GET /admin/library/incoming` shows the assignment per queued file and a
`by_group` tally, so you can confirm the filing before spending hours indexing.

Individual uploads and a batch-wide default work the same way:

```bash
# One document
curl -s -X POST "$SPK_URL/admin/library/upload?group=discipline-knowledge" \
  -H "Authorization: Bearer $SPK_TOKEN" -F "file=@Handbook.pdf"

# Everything queued that has no assignment of its own
curl -s -X POST "$SPK_URL/admin/library/ingest" \
  -H "Authorization: Bearer $SPK_TOKEN" -H "Content-Type: application/json" \
  -d '{"group":"discipline-knowledge"}'
```

An assigned group outranks every form of inference. Valid names are
`engineering`, `contracting-law`, `discipline-knowledge`, and `miscellaneous`;
anything else is a 400 listing the valid ones, so a typo cannot silently misfile a
batch.

### Adding titles to the Pratt library

The Pratt library is a chosen collection, so filing is the only way in. Upload the
folder onto Miscellaneous as above, then name the titles that belong in the
collection:

```bash
python3 scripts/file_library_documents.py --group discipline-knowledge \
  --matching "Advances in" --matching "Cost Estimation" \
  --matching "Designing Data" --matching "Algorithmic Trading"
```

That prints the titles it would move and writes nothing. `--matching` is a
substring search, so widen or narrow the patterns until the printed list is the
collection you want, then run the same command again with `--apply`. Use
`--source` with an exact filename instead when you know it.

On Windows the interpreter is `python`, and PowerShell needs the line breaks
written as backticks — or just put it on one line:

```powershell
python scripts\file_library_documents.py --group discipline-knowledge --matching "Advances in" --matching "Cost Estimation" --matching "Designing Data" --matching "Algorithmic Trading"
```

### Moving documents that are already indexed

Correcting where a document files does not require re-embedding it.
`/admin/library/regroup` rewrites chunk metadata in place, which matters when the
index holds thousands of documents:

```bash
# See what would move, change nothing
python3 scripts/file_library_documents.py --group discipline-knowledge \
  --matching "Student Slides"

# Do it
python3 scripts/file_library_documents.py --group discipline-knowledge \
  --source "004 FY26 Student Slides.pdf" --apply
```

`--source` takes exact indexed filenames; `--matching` matches substrings against
them. Both are repeatable and can be combined. The endpoint behind this is
`POST /admin/library/regroup`, which takes `sources`, `patterns`, `from_group`,
`inferred_only` and `dry_run` if you would rather call it directly.

### Sweeping a whole page

`from_group` selects everything currently on a page, which is the bulk form of the
same correction — useful for emptying Miscellaneous Documents into a subject index
once you have looked at what is on it:

```bash
# What is on the page that nobody filed there?
python3 scripts/file_library_documents.py --group engineering \
  --from-group miscellaneous --inferred-only
```

`--inferred-only` spares the documents that were filed on that page deliberately.
Drop it and `--from-group` takes the whole page, deliberate filings included — so
keep it unless you mean that.

`GET /library/groups/<page>` reports `assigned_count` (filed there on purpose) next
to `inferred_count` (landed there by a rule), which is how you tell a collection
from a catch-all. Discipline Knowledge should always read `inferred_count: 0`,
since nothing routes a document there.

The fallback page itself can be moved without a redeploy, in
`{DATA_DIR}/library_groups.json`:

```json
{
  "default": "engineering",
  "categories": { "misc": "engineering", "course-material": "engineering" }
}
```

`default` catches documents with no category at all; the `categories` entries
catch `misc` (nothing could be inferred) and `course-material` (the filename
looked like training material). All three go to Miscellaneous Documents out of the
box, so this is only needed if you would rather they landed elsewhere. An
unrecognized page name in this file is ignored rather than applied, so a typo
cannot empty a page.

### Retuning which index a document lands in

Documents with no assignment are routed by the `category` inferred from their
filename, and AR/PAM are routed by series number (AR 420-1 is facilities
engineering; AR 27-1 is legal services). To change that routing without a
redeploy, drop a `library_groups.json` file in the data volume
(`/data/files/library_groups.json`):

```json
{
  "categories":          { "course-material": "engineering" },
  "doc_number_prefixes": { "AR 385": "discipline-knowledge" },
  "sources":             { "004 FY26 Student Slides.pdf": "engineering" }
}
```

Full precedence, most specific first: an exact `sources` match, then the group
assigned at upload time, then the longest `doc_number_prefixes` match, then
`categories`, then the built-in rules. An exact filename beats a batch assignment
because it is the narrower statement of the two.

Valid group names are `engineering`, `contracting-law`, `discipline-knowledge`,
and `miscellaneous`; unknown names and malformed JSON are ignored so a bad edit
cannot blank out an index. The file is read once per process, so restart the
service (or redeploy) to pick up changes.

Pointing a `categories` or `doc_number_prefixes` rule at `discipline-knowledge`
does work, but it makes the reading collection something a filename can route into
— which is the property the collection exists without.

## Verify deployment

```bash
curl https://YOUR-APP.up.railway.app/health
```

Expect `"status":"ok"` and `"auth_required":true` (the access roster is active).
