# Project SPK on the Windows laptop

The same app that runs on the Mac mini also runs on a Windows laptop, for use
with no network. Ollama on that laptop does the answers, the embeddings, and
the scanned-page reading. Questions and documents stay on the laptop.

Do this setup while you still have a connection. After the models are on disk
and the library is indexed, the laptop does not need the internet, Railway, or
the OpenAI API.

The app listens only on this laptop (`127.0.0.1`). That is deliberate. The Mac
mini listens on the local network so a second person can open it; a laptop in
austere conditions should not.

Sign-in still checks the roster in `app/config.py`. Typing the email does not
send a message anywhere. It works offline.

## One-time setup

Install both of these and leave "Add to PATH" checked:

- [Ollama for Windows](https://ollama.com/download/windows). Open the app once.
- [Python 3.12 for Windows](https://www.python.org/downloads/windows/).

Then, in PowerShell, from a checkout of this repo:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

The script reads RAM and, when `nvidia-smi` is present, GPU memory. It pulls a
chat model, a vision model, and `nomic-embed-text`, creates `.venv`, and
writes `.env` from `.env.windows.example`.

| Machine | Chat | Scanned pages |
| --- | --- | --- |
| Under 16 GB RAM, no useful GPU | `qwen2.5vl:3b` (one model does both) | same model |
| 16 GB RAM, or a GPU with 6–15 GB | `qwen2.5:7b-instruct` | `qwen2.5vl:3b` |
| 32 GB RAM, or a GPU with 16 GB and up | `qwen2.5:14b-instruct` | `qwen2.5vl:7b` |

Force a model with `-Model qwen2.5:14b-instruct`. A model that does not fit in
GPU memory falls off onto the CPU and gets slow. Stay inside the row above
unless you have tried the larger one and it stays responsive.

Leave Ollama on localhost. Do not set `OLLAMA_HOST` to `0.0.0.0`.

## Put the library on the laptop before you leave

The Railway index cannot be copied. It was embedded with
`text-embedding-3-small`. This laptop embeds with `nomic-embed-text`, the same
model as the Mac mini, with the same prefixes, so a finished Mac index can be
copied. The Mac has to have rebuilt that index first. See [MAC_MINI.md](MAC_MINI.md).

Stop the app on both machines, then copy two folders:

| On the Mac | On the laptop, inside the repo |
| --- | --- |
| `chroma_db_mac\` | `chroma_db_laptop\` |
| `data\` | `data\` |

An external drive is enough. On the laptop, from the repo folder:

```powershell
robocopy E:\spk-index chroma_db_laptop /E
robocopy E:\spk-data data /E
```

If the Mac has not finished its index yet, build one on the laptop instead.
Stop the app first. Chroma cannot be written by two processes:

```powershell
.\.venv\Scripts\python.exe scripts\bulk_ingest.py "D:\path\to\documents"
```

That skips files already indexed and can be re-run if it stops. A couple of
thousand PDFs takes hours. Do it before you are offline. Changing
`OPENAI_EMBEDDING_MODEL` later means deleting `chroma_db_laptop\` and doing
it again.

## Run it

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Start-ProjectSPK.ps1
```

That starts Ollama if the app is not already running, starts Project SPK, and
opens it at <http://127.0.0.1:8000>. A desktop shortcut does the same thing:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Install-ProjectSPKShortcut.ps1
```

Stop the app with `scripts\windows\Stop-ProjectSPK.ps1`. That leaves Ollama
running so the next launch does not reload the model.

To try a different chat model later, while online or with the model already
downloaded:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\Switch-LocalModel.ps1 qwen2.5:14b-instruct
```

Then stop and start the app. That does not touch the embedding model.

## What stays on the machine

- No call to `api.openai.com`. `OPENAI_BASE_URL` points at Ollama on this laptop.
- `OPENAI_API_KEY=ollama` is a placeholder. Ollama ignores it.
- `OPENAI_VISION_MODEL` reads scanned pages. The chat model cannot.
- Chroma telemetry is off (`ANONYMIZED_TELEMETRY=False`).
- `HOST=127.0.0.1`. Leave it there in the field. `0.0.0.0` would show the
  login page to anyone else on that network.

If a question you know the answer to comes back with the right citation, this
laptop is ready to leave the network. The Mac mini can keep serving at home.
Stop Railway only after the Mac has passed that same check.
