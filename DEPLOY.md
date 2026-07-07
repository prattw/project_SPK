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
| `USAGE_DB_PATH` | | `/app/data/usage.db` — login/query/upload analytics |
| `USAGE_ADMIN_EMAILS` | | Comma-separated emails allowed to call `GET /usage/summary` |

Generate a strong `AUTH_SECRET` (e.g. `openssl rand -hex 32`). Without it,
login sessions are invalidated on every restart/redeploy (users just sign in again).

### Access roster

Sign-in is restricted to the roster of approved `@usace.army.mil` emails
(built into `app/config.py`, override with `ACCESS_ROSTER`). Users sign in
with their email and get a signed session token that expires after 24 hours,
after which they must sign in again.

### Usage analytics

The app logs logins, query latency, token usage, and uploads to a SQLite file
(`USAGE_DB_PATH`, default `./data/usage.db`). Put this on the same persistent
volume as `/app/data` so records survive redeploys.

Administrators (`USAGE_ADMIN_EMAILS`) can review activity:

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://YOUR-APP.up.railway.app/usage/summary
```

Tracked fields include who logged in, per-query prompt-to-answer time, token
counts (LLM + embeddings), and uploaded file names/sizes.

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

## Verify deployment

```bash
curl https://YOUR-APP.up.railway.app/health
```

Expect `"status":"ok"` and `"auth_required":true` (the access roster is active).
