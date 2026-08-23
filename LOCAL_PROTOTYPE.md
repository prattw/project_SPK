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

| | This laptop prototype | Eventual RTX 5090 / PRO 6000 box |
|---|---|---|
| Model size | 3B–14B (RAM/GPU-dependent) | 14B–70B+ class |
| Context budget | Kept modest (`MAX_CONTEXT_CHARS=40000`) | Expanded (60k–100k+ tokens) as planned |
| Speed | Likely slow, especially on CPU-only | Fast — that's the whole point of the GPU purchase |
| Purpose | Learn the setup, test prompts/retrieval tuning, validate the Ollama swap works | Production-grade self-hosted serving for the team |

Treat this as a rehearsal, not a preview of final quality or speed. A
laptop CPU (or a small laptop GPU, if your ThinkPad has a discrete NVIDIA
card) is not the hardware that was sized for your actual requirements —
the point of this exercise is to de-risk the *software* setup (Ollama,
env vars, re-indexing, prompt behavior) before spending money on hardware.

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

Running Windows instead of Linux on the laptop? Do this inside **WSL2**
(Ubuntu) — install WSL2, open an Ubuntu terminal, and run the same script
there. Ollama's Linux install works unmodified inside WSL2.

## Running it

```bash
source .venv/bin/activate
./start.sh
# open http://127.0.0.1:8000
```

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
