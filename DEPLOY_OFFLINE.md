# Project SPK — Austere / Offline Deployment (standalone laptop)

Goal: a USACE employee can carry a laptop on deployment and use Project SPK
with **zero internet connectivity** — no Railway, no OpenAI, no Azure. This
is a separate effort from the Azure Government/CUI migration (`government`
branch) and does not depend on it. Target hardware for the first build:
a **2019 MacBook Air (Intel)** — no discrete GPU, no Apple Silicon Neural
Engine, CPU-only inference.

## Why this is a small change, not a rewrite

Auditing the app's network dependencies, almost everything is already local:

| Component | Network dependency today? |
|---|---|
| Document parsing (pdf/docx/xlsx/pptx/images) | None — all local (`pypdf`, `pymupdf`, `python-docx`, etc.) |
| Vector index (Chroma) | None — local disk (`chroma_db/`) |
| Usage analytics (SQLite) | None — local disk (`data/usage.db`) |
| Auth (roster + signed token, or WebAuthn) | None — self-contained HMAC/FIDO2, no external identity provider call |
| Publication sync (`check_publication_sites`) | Yes, but **only when explicitly called** via `POST /sync/publications` — never automatic at startup. Simply never call it offline. |
| Citation links (usace.army.mil URLs shown in answers) | Cosmetic only — links just won't be clickable offline; doesn't block anything |
| **Chat completions (LLM)** | **Yes — OpenAI API** |
| **Embeddings** | **Yes — OpenAI API** |
| **Vision/OCR fallback for scanned pages** | **Yes — OpenAI vision model** |

Only the three bolded rows need a real local replacement. And because
`app/config.py` already has an `OPENAI_BASE_URL` override (originally added
for "a proxy or a local gateway"), pointing the *existing* OpenAI client code
at a local **Ollama** server mostly works with **no code changes** — Ollama
exposes an OpenAI-compatible `/v1/chat/completions` and `/v1/embeddings` API.

## Architecture

```
MacBook Air (offline, no network needed beyond localhost)
├── Ollama (local model server, http://127.0.0.1:11434)
│     ├── chat model   (e.g. llama3.2:3b — see hardware note below)
│     └── embedding model (e.g. nomic-embed-text — small, fast on CPU)
├── Project SPK (this app), uvicorn on http://127.0.0.1:8000
│     ├── OPENAI_BASE_URL=http://127.0.0.1:11434/v1
│     ├── OPENAI_API_KEY=ollama          (dummy — Ollama ignores it)
│     ├── OPENAI_MODEL=llama3.2:3b
│     └── OPENAI_EMBEDDING_MODEL=nomic-embed-text
├── chroma_db/  (pre-seeded vector index, copied on before departure)
└── data/       (pre-seeded document library, copied on before departure)
```

Browser on the same laptop hits `http://127.0.0.1:8000` — no LAN, no Wi-Fi,
no cell connection required at any point after setup.

## Hardware constraint (read this before picking a model)

2019 MacBook Air: dual-core Intel i5/i7, no discrete GPU, 8 GB or 16 GB RAM
depending on configuration (**confirm which one the target laptop has** —
materially changes which model is usable). CPU-only inference on this class
of chip is slow — expect a noticeably slower, less capable assistant than
the cloud version. Set that expectation with the end user up front.

| RAM | Recommended chat model | Notes |
|---|---|---|
| 8 GB | `llama3.2:3b` (Q4, ~2 GB) or `phi3.5:3.8b` | Safe default; still slow on CPU-only (rough estimate: a few tokens/sec) but usable for short Q&A |
| 16 GB | `qwen2.5:7b` (Q4, ~4.5 GB) or `llama3.1:8b` | Noticeably better answer quality; still CPU-bound so slower than cloud |

Embedding model either way: `nomic-embed-text` (~275 MB) — small and fast
enough on CPU that it won't be the bottleneck.

Vision/OCR for scanned PDF pages is **not in scope for v1** — it would need
a second, larger multimodal model (e.g. `llava` or `moondream`) running
alongside the chat model, which is a lot to ask of this hardware. Scanned
(image-only) pages simply won't be searchable offline; typed/selectable-text
PDFs are unaffected.

## Setup (must be done *while still on a network*, before departure)

1. Install Ollama and pull models — see `scripts/setup_offline_macbook.sh`
   (run it once, on a network connection, on the actual deployment laptop).
2. Install Python + this app's dependencies (`pip install -r requirements.txt`)
   — also needs network, so do this before departure too.
3. Seed the document library: copy a pre-built `chroma_db/` and `data/`
   folder onto the laptop (built ahead of time, e.g. from a local ingest run
   or copied from production — **do not put CUI-marked documents in an
   offline kit shared/handled outside proper controls**, same caution as the
   cloud deployment).
4. Copy this repo (or a release build of it) onto the laptop.
5. Create `.env` from `.env.example` with the Ollama settings shown above.

## Running it in the field (no network required)

```bash
ollama serve &          # if not already running as a background service
./start.sh               # same script as local dev
# Open http://127.0.0.1:8000 in a browser on the same laptop
```

Auth: keep the roster/signed-token login as-is (works fully offline — no
external identity provider call) or disable the roster (`ACCESS_ROSTER=`
empty) for a single-user offline kit where a login screen adds no value.

## Known limitations (set expectations)

- Answer quality and speed are well below the cloud version — CPU-only
  inference on a small quantized model, not GPT-4o-mini/GPT-5.
- No scanned-page OCR (see above).
- No publication sync / live citation link verification (harmless — those
  are cosmetic or explicitly admin-triggered, never automatic).
- No weekly usage report delivery anywhere (still logs locally to
  `data/usage.db`; nothing pulls it off the laptop unless someone does that
  manually later).
- The document library is a snapshot from whenever it was seeded — no way to
  add newly published UFC/ER/AR content without network access.

## Open items

- [ ] Confirm actual RAM on the target laptop (8 GB vs 16 GB) — determines
  final model choice above.
- [ ] Decide what document library snapshot ships on the laptop (all of
  production? A curated subset for the deployment's mission set?).
- [ ] Test actual tokens/sec on the real hardware before relying on it —
  the table above is an estimate, not a benchmark.
