# Project SPK — Construction Document RAG

Chat-style app for construction teams: upload PDFs, P6 schedules, IFC models, and more — then ask questions or compare documents. Powered by **OpenAI** (GPT + embeddings).

## Recommended stack (your questions)

### Hosting

| Option | Best for | Why |
|--------|----------|-----|
| **[Railway](https://railway.app)** | **Start here** | Docker deploy, persistent volumes for `chroma_db` + `data`, simple env vars, good for 100MB uploads |
| **[Fly.io](https://fly.io)** | Global / low latency | Same as Railway; volumes + regions; slightly more ops |
| **Render** | Managed simplicity | Similar to Railway; watch cold starts on free tier |
| **AWS (ECS/EC2) + S3** | Enterprise / large files | When you need SSO, VPC, or many GB of drawings |

**Avoid** serverless-only hosts (e.g. Vercel functions) as the primary API — RAG needs a long-running process and disk for Chroma.

**Practical pick:** Railway or Fly.io with Docker, persistent volume, and later S3 for raw uploads if files grow past ~100MB.

### LLMs

- **Answers + embeddings:** [OpenAI API](https://platform.openai.com/) — GPT (`gpt-4o-mini` by default) and `text-embedding-3-small` with a single `OPENAI_API_KEY`.

**Note:** ChatGPT Plus/Pro is not the same as the API — you need an API key from the OpenAI platform (pay-as-you-go; often a few dollars for testing).

### UI

A minimal ChatGPT-style UI is included at `/` — upload sidebar, chat, source citations.

## Supported files

| Type | Extensions | Notes |
|------|------------|--------|
| Drawings / specs | `.pdf` | Full text extraction |
| Primavera P6 | `.xer`, `.xml` | Schedule tasks, WBS, relationships |
| BIM | `.ifc` | Names / entities (lightweight parse) |
| 3D | `.gltf` | Metadata; `.glb` stored with guidance |
| Revit / CAD | `.rvt`, `.dwg`, `.dxf`, … | Stored + indexed summary; export **IFC** or **PDF** for deeper Q&A |

## Quick start

```bash
cd "Project SPK"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set OPENAI_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** — upload files and chat.

## Deploy online

See **[DEPLOY.md](DEPLOY.md)** for a full Railway guide (volumes, env vars, public URL).

Quick checklist:

1. Push to GitHub
2. Railway → deploy from repo (`Dockerfile` + `railway.toml`)
3. Set `OPENAI_API_KEY`, `APP_API_KEY`
4. Mount volumes at `/app/chroma_db` and `/app/data`
5. Share the public URL and team access key

```bash
docker compose up --build -d   # local production test
```

## Local development

```bash
./start.sh
# Open http://127.0.0.1:8000
cp .env.example .env   # add API keys for upload/chat
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Chat UI |
| GET | `/health` | Status |
| GET | `/files` | Indexed filenames |
| POST | `/upload` | Upload + index one file |
| POST | `/query` | `{"question": "..."}` |
| POST | `/reset` | Clear index |

## 2000-page PDFs

Built for large construction spec sets:

1. **Page-by-page indexing** — Each PDF page becomes one or more searchable chunks with `page` metadata (not one giant blob).
2. **Background ingest** — PDFs with **75+ pages** upload immediately; indexing runs in a background job with progress (`GET /jobs/{id}`). The UI polls until complete.
3. **Page-aware questions** — e.g. *“What does page 842 say about concrete curing?”* boosts that page in retrieval.
4. **Bounded answers** — GPT still receives only the top relevant chunks (~120k characters), not all 2000 pages.

| Setting | Default | Notes |
|---------|---------|--------|
| `MAX_PDF_PAGES` | 2500 | Raise if you need more than 2500 pages indexed |
| `MAX_CHUNKS_PER_FILE` | 6000 | ~3 chunks/page worst case for dense sheets |
| `MAX_UPLOAD_MB` | 300 | Increase on Railway if uploads fail |
| `PDF_BACKGROUND_PAGE_THRESHOLD` | 75 | Pages above this → background job |

**Expectations:** Indexing 2000 pages can take **10–30+ minutes** (embedding cost/time). Queries about a **topic** work well; *“summarize every page”* in one shot does not — that needs a separate reporting pipeline.

**Hosting:** Use a persistent volume and enough RAM (2GB+). Increase HTTP/proxy timeouts for large uploads.

## Compare two documents

Upload both files, then ask naturally:

- *Compare the submittal PDF with the spec section 08 44 00.*
- *Does the P6 schedule include the same milestones as the contract PDF?*

Retrieval pulls relevant chunks from all indexed files; GPT answers with citations.

## Environment

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | Required (chat + embeddings) |
| `OPENAI_MODEL` | Default `gpt-4o-mini` |
| `OPENAI_EMBEDDING_MODEL` | Default `text-embedding-3-small` |
| `MAX_UPLOAD_MB` | Default `300` |

## Roadmap ideas

- Auth (API keys / login) before public launch
- S3 upload + async indexing for large drawing sets
- Dedicated Revit/IFC pipeline (IfcOpenShell, Autodesk Design Automation)
- Multi-project workspaces

XXX
Links to USACE Publicactions:
https://www.usace.army.mil/Resources/Library/
https://www.usace.army.mil/Resources/Library/Library-Program/
https://www.publications.usace.army.mil/
https://www.erdc.usace.army.mil/Library.aspx
https://geospatial-usace.opendata.arcgis.com/