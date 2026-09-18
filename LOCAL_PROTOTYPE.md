# Project SPK — Personal Local Prototype (Lenovo laptop)

Purpose: give you a working sandbox to test the self-hosted architecture
(local model instead of the OpenAI API) on your own Lenovo laptop, in the
run-up to buying an RTX 5090 or RTX PRO 6000 Blackwell workstation. This is
**not** a change to production — Project SPK on Railway keeps using the
OpenAI API exactly as it does today. This is a second, independent copy of
the app that only you run, only on your laptop.

This laptop copy is meant to work like a standalone desktop app — closer to
Word than to a hosted web service:

- **Everything runs locally.** No OpenAI API call is ever made — chat,
  embeddings, and vision/OCR all go to a model running on this laptop via
  Ollama. See "Working fully offline" below for exactly what that does and
  doesn't require internet for.
- **The model is swappable.** Change it any time with one command — see
  "Swapping the model" below — without touching any app code.
- **It has a desktop icon** (see the section further down) so it opens like
  a normal app, not a terminal + browser tab.
- **It can run an experimental agent mode** that lets the model search the
  library, read a document, and draft a report on its own — a first
  prototype of the "agents on government terminals" feature planned for the
  hosted app. See "Agent mode" below.

## Why this works with (almost) no code changes

`app/config.py` already exposes `OPENAI_BASE_URL`, and both `app/llm.py`
and `app/embeddings.py` build the OpenAI client from it:

```python
client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
```

Ollama serves an OpenAI-compatible API, so pointing `OPENAI_BASE_URL` at
`http://127.0.0.1:11434/v1` makes the existing chat, embeddings, and vision
OCR code paths talk to a model running on your laptop instead of OpenAI's
servers. No app code changes — just a different `.env`.

## What you get vs. what you're prototyping

| | Laptop, CPU-only | Laptop with a small GPU (e.g. 8GB RTX 5050/4060) | Eventual RTX 5090 / PRO 6000 box |
|---|---|---|---|
| Model size | 3B | 7B–8B, fully in VRAM | 14B–70B+ class |
| Context budget | Kept modest (`MAX_CONTEXT_CHARS=40000`) | Modest, but the model itself answers faster | Expanded (60k–100k+ tokens) as planned |
| Speed | Slow | Meaningfully faster — real CUDA acceleration, same code path as the production box | Fast — that's the whole point of the GPU purchase |
| Purpose | De-risk the software setup | De-risk the setup *and* get a legitimate (if small-scale) preview of GPU-accelerated serving | Production-grade self-hosted serving for the team |

If your laptop has a discrete NVIDIA GPU, this stops being a pure software
rehearsal — Ollama running through CUDA on that GPU is the same mechanism
the 5090/PRO 6000 box will use, just with far less VRAM (8GB vs. 32–96GB).
That means smaller models and a smaller context window, but the *speed
character* (fast decode, prefill still the main wait) will feel closer to
the real thing than CPU-only inference ever would.

## One-time setup

While connected to the internet:

```bash
./scripts/setup_local_prototype.sh
```

This installs Ollama, detects your RAM (and GPU, if any), pulls a
right-sized chat model and the `nomic-embed-text` embedding model, creates
a Python virtualenv, installs dependencies, and writes `.env` from
`.env.local.example`.

Force a specific model instead of auto-detection:

```bash
./scripts/setup_local_prototype.sh --model qwen2.5:14b-instruct
```

### Running Windows instead of Linux? Use WSL2

Do all of this inside **WSL2** (Ubuntu) — Ollama's Linux install works
unmodified there, and if your laptop has a discrete NVIDIA GPU, WSL2 passes
it through so Ollama gets real CUDA acceleration, not just CPU.

**1. Install WSL2 with Ubuntu** (from an elevated PowerShell):

```powershell
wsl --install -d Ubuntu
```

Reboot if prompted, then open the "Ubuntu" app from the Start menu and
finish the first-run username/password setup.

**2. If you have a discrete NVIDIA GPU, verify passthrough works.** You do
**not** install a separate Linux NVIDIA driver inside WSL2 — the Windows
driver handles it. Just confirm it's visible from inside Ubuntu:

```bash
nvidia-smi
```

If that shows your GPU and its VRAM, you're set. If it's not found, update
to the latest NVIDIA driver on the **Windows** side (Game Ready or Studio
driver, whichever you already use) and try again — recent drivers on
Windows 11 support this automatically.

**3. Give WSL2 enough memory.** By default WSL2 caps itself at a fraction
of your system RAM, which can starve a 7B+ model. Create/edit
`C:\Users\<you>\.wslconfig` (in Windows, not inside Ubuntu):

```ini
[wsl2]
memory=24GB
processors=12
```

Then from PowerShell: `wsl --shutdown`, and reopen the Ubuntu app.

**4. Get the code and run setup, inside Ubuntu:**

```bash
git clone https://github.com/prattw/project_SPK.git
cd project_SPK
git checkout cursor/laptop-local-prototype-a548   # this branch, until merged
./scripts/setup_local_prototype.sh
```

The script's GPU-detection will pick a 7-8B model automatically if it sees
an 8GB-class laptop GPU (e.g. an RTX 5050/4060) — that's the sweet spot: it
fits fully in VRAM with room for a modest context window, rather than
spilling to slow CPU offload the way a 14B model would on 8GB.

**5. Run the app** and open it from your normal Windows browser — WSL2
forwards `localhost` automatically:

```bash
source .venv/bin/activate
./start.sh
```

Then browse to `http://127.0.0.1:8000` from Windows, same as if it were
running natively.

## Running it

```bash
source .venv/bin/activate
./start.sh
# open http://127.0.0.1:8000
```

## Swapping the model

The model is intentionally not hardcoded anywhere in the app — `app/llm.py`
and `app/embeddings.py` just read whatever `OPENAI_MODEL` (and
`OPENAI_EMBEDDING_MODEL`) says in `.env`. To try a different chat model:

```bash
./scripts/switch_local_model.sh qwen2.5:14b-instruct
```

This pulls the model with Ollama if it isn't local yet, and updates
`OPENAI_MODEL` in `.env` for you. Restart the app (`./start.sh`) afterward.
Pick a size that fits your laptop's RAM/VRAM — see the sizing table above.

A couple of things to know when swapping:

- **Embedding model swaps are riskier.** Changing `OPENAI_EMBEDDING_MODEL`
  after documents are already indexed makes the existing vectors in
  `chroma_db_local/` incompatible with new queries (different model, different
  vector space). If you change the embedding model, delete `chroma_db_local/`
  and re-ingest.
- **Agent mode needs a tool-calling model.** If you turn on Agent mode (next
  section), the model must support OpenAI-style tool/function calling.
  Qwen2.5, Llama 3.1+, and Mistral-Nemo all do; check
  [ollama.com/search?c=tools](https://ollama.com/search?c=tools) for the
  current list before switching.

## Agent mode (experimental, tool-calling)

`.env.local.example` sets `ENABLE_AGENT_MODE=true` on this laptop prototype
only — production on Railway has this off (`ENABLE_AGENT_MODE` defaults to
`false`) and doesn't show any of this. It's a first, deliberately small
prototype of the "agents on government terminals" idea for the hosted app:
instead of one retrieval pass + one answer, the model can take a few steps
on its own before responding.

When it's on, an **"Agent mode"** checkbox appears above the chat box. With
it checked, the model can call these tools, entirely against your local
index — no network calls, no code execution, no arbitrary file access:

| Tool | What it does |
|---|---|
| `search_documents` | Semantic search over the local library (same retrieval the normal chat uses) |
| `list_documents` | Lists every indexed document's filename, doc number, title, category |
| `read_document` | Reads one specific document's indexed text in page order (for "summarize this file") |
| `draft_docx_report` | Writes a Word document to `data_local/_agent_output/` on this laptop |

The chat reply shows a collapsible **"Agent steps"** trace above the answer
so you can see exactly which tools it called and in what order — useful
both for trust (did it actually check the documents?) and for debugging a
model that isn't calling tools the way you'd expect.

Known limitations of this first pass:

- **Smaller/CPU-tier models are unreliable at tool calling.** A 3B model
  will often ignore the tools entirely or produce malformed calls. This
  works best on the 7B+ tier models the setup script picks for 16GB+ RAM or
  an 8GB+ GPU.
- **It's capped at a few steps per turn** (`AGENT_MAX_STEPS=6` in `.env`) so
  a confused model can't loop forever — if it runs out of steps, it's asked
  once more, without tools, to just answer with whatever it's found so far.
- **This is a prototype, not the government-terminal agent feature itself.**
  It has no access to anything outside this app's own document index and
  output folder — no shell, no arbitrary filesystem, no other applications.
  Treat it as a testbed for what a tool-using model on local hardware can
  and can't do reliably, ahead of designing the real feature.

## Desktop icon / app-like window (Windows)

Once you've confirmed the app runs via `./start.sh` at least once (above),
you can set up a normal-looking desktop icon so you don't have to open a
terminal every time. This is entirely on the Windows side — it starts the
WSL2 backend for you in the background and opens the app in a chromeless
browser window (no address bar or tabs), so it looks and feels like a
standalone app rather than a browser tab.

Files live in `scripts/windows/` in this repo:

| File | Purpose |
|---|---|
| `app-icon.ico` | The icon used for the shortcut |
| `Start-ProjectSPK.ps1` | Starts the backend in WSL2 if needed, opens the app window |
| `Stop-ProjectSPK.ps1` | Stops the backend inside WSL2 |
| `Install-ProjectSPKShortcut.ps1` | One-time installer — creates the Desktop/Start Menu icon |

**Install (run once):** open the repo folder in File Explorer — from the
address bar, go to `\\wsl.localhost\Ubuntu\home\<you>\project_SPK\scripts\windows`
(swap `<you>` for your WSL username) — then open a PowerShell window there
(Shift+Right-click the folder background → "Open PowerShell window here")
and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-ProjectSPKShortcut.ps1
```

This copies the launcher and icon to `%LOCALAPPDATA%\ProjectSPK` (so the
shortcut doesn't depend on the WSL2 network path staying mounted) and
creates a **"Project SPK"** icon on your Desktop and in the Start Menu.
Windows always opens `.ps1` files in Notepad by default when
double-clicked — that's expected; running it via the command above is the
one-time exception, and it only affects this script's process, not your
system's execution policy.

**Use it:** double-click the "Project SPK" icon. First launch takes a few
seconds while the backend starts inside WSL2 (subsequent launches are
faster if it's already running). To pin it to the taskbar, right-click the
Desktop icon and choose **Pin to taskbar**.

**Stop it:** run `Stop-ProjectSPK.ps1` from `%LOCALAPPDATA%\ProjectSPK`, or
just `wsl --shutdown` from PowerShell to stop everything running in WSL2.

This is purely a Windows-side convenience layer — it doesn't change
anything about the app itself, and macOS/Linux versions of "double-click
to launch" would use the equivalent native mechanism (an `.app` bundle or
a `.desktop` file) if you ever need one.

## Important: separate data, separate index

`.env.local.example` points `CHROMA_PERSIST_DIR` at `chroma_db_local/` and
`DATA_DIR` at `data_local/` — deliberately different from the folders your
regular local dev checkout might use. This avoids two problems:

1. **Embedding incompatibility.** Ollama's `nomic-embed-text` produces
   different vectors (different dimensions, different space) than OpenAI's
   `text-embedding-3-small`. A Chroma index built with one is meaningless
   to the other. Never point this prototype at a `chroma_db/` that was
   built against OpenAI embeddings.
2. **No accidental production data.** This is a personal sandbox — upload
   test documents you don't mind experimenting with, not the live
   production document library.

To try it out, upload a PDF or two through the UI and ask questions, same
as the production app. Expect noticeably slower answers and a less capable
model than the Railway/OpenAI version — that's the hardware, not a bug.

## Bulk-loading a document library (e.g. a "Master Library" folder)

If you already have a folder of documents on the laptop you want indexed —
rather than uploading files one at a time through the UI — use
`scripts/bulk_ingest.py`. It walks a folder recursively, indexes every
supported file, and is safe to interrupt and re-run (it skips filenames
already in the index).

**1. Stop the app first.** ChromaDB doesn't support two processes writing
to the same index at once, and `./start.sh` holds it open:

```bash
# In the terminal running ./start.sh, press Ctrl+C.
# Or, from another terminal:
pkill -f uvicorn
```

**2. Find the folder from inside WSL2.** Windows drives are mounted under
`/mnt/<drive letter>/`, so a folder that's `C:\Users\CYRUS\Documents\Master
Library` in Windows Explorer is this path inside Ubuntu:

```bash
/mnt/c/Users/CYRUS/Documents/Master Library
```

(Swap `CYRUS` and the folder name for whatever's actually in your path —
this works the same way for any folder under any Windows drive letter.)

**3. Run the bulk ingest**, quoting the path because it has a space in it:

```bash
source .venv/bin/activate
python scripts/bulk_ingest.py "/mnt/c/Users/CYRUS/Documents/Master Library"
```

It prints one line per file (`OK`, `SKIP`, or `FAIL`) plus a running count,
so you can watch progress or leave it running in the background for a large
library. Supported file types: `.pdf .docx .xlsx .csv .pptx .txt .md .xml
.xer .ifc`, plus common image/CAD formats stored for reference. Unsupported
files are skipped, not treated as errors.

**4. Restart the app** once it finishes:

```bash
./start.sh
```

Your uploaded/ingested documents now live in `chroma_db_local/` and
`data_local/` on this laptop only — see the embedding-incompatibility note
above for why this can never be merged with production's index.

## Working fully offline

Once setup is done, day-to-day use needs **no internet at all** — chat,
uploads/ingest, the document library, and Agent mode all run against
services on `127.0.0.1` (the app, Ollama, and the local Chroma index).
Nothing about answering a question or drafting a report calls out anywhere.

What still needs internet, and only ever once:

- **Initial setup** (`./scripts/setup_local_prototype.sh`) — installing
  WSL2/Ubuntu, installing Ollama, and `ollama pull`-ing the models. After
  that, the models live on disk under Ollama's local model store.
- **Switching to a new model** (`./scripts/switch_local_model.sh`) — same
  reason, it's a one-time download of that model.
- **Installing the desktop shortcut** the first time — copying files
  locally, no network involved, but if you haven't cloned the repo yet
  that step obviously needs it.

A couple of optional features in the UI *do* reach out to the internet if
you use them, and will just fail harmlessly (an error message, nothing
crashes) if you click them with no connection:

- The **"Sync Publications"** button checks USACE/federal publication
  websites for newly listed documents — not needed for chat or search to
  work, only for that specific update-check feature.
- Links in the **Document Library** tab that point to
  `publications.usace.army.mil` or `usace.army.mil` open the public USACE
  site in a browser tab — informational only, not something the app itself
  depends on.

Everything else — including the desktop icon launch, WSL2 backend startup,
and the chromeless app window — works completely offline, the same way a
locally installed desktop application does. This is the same self-hosted
pattern a separate effort (the `austere-offline` branch) built for a
different laptop aimed at field/no-internet deployment; that branch is
about packaging a *standalone kit* for someone else to carry, while this
one is about your own development/testing laptop, but the underlying
"local Ollama model instead of OpenAI" mechanism is identical.

## What this does *not* cover

- **Vision OCR for scanned pages.** Works the same way (routes through the
  same `OPENAI_MODEL`), but a small local model doing OCR/vision on a
  laptop CPU will be slow and lower quality than GPT-4o-mini. Fine for
  testing that the code path works; don't judge OCR quality by it.
- **Production changes of any kind.** Nothing here touches `railway.toml`,
  `railway.json`, the `Dockerfile`, or any Railway environment variable.

## When the RTX 5090 / PRO 6000 box arrives

The setup pattern carries over directly: same `OPENAI_BASE_URL` trick,
same Ollama (or vLLM, for more throughput) backend, just bigger models and
a larger `MAX_CONTEXT_CHARS`. Anything you learn tuning retrieval settings
or prompts on this laptop prototype should transfer to that box with
little more than an `.env` change.
