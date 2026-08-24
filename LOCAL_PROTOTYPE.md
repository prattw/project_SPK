# Project SPK — Personal Local Prototype (Lenovo laptop)

Purpose: give you a working sandbox to test the self-hosted architecture
(local model instead of the OpenAI API) on your own Lenovo laptop, in the
run-up to buying an RTX 5090 or RTX PRO 6000 Blackwell workstation. This is
**not** a change to production — Project SPK on Railway keeps using the
OpenAI API exactly as it does today. This is a second, independent copy of
the app that only you run, only on your laptop.

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

## What this does *not* cover

- **Offline/no-internet field use.** A different effort
  (`austere-offline` branch) targets that specific scenario for a
  standalone deployment laptop; this prototype assumes you have normal
  internet access, it's just not calling OpenAI for inference.
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
