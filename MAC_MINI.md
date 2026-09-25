# Project SPK on a Mac mini

The app runs on the Mac mini, and the same way on a Windows laptop when there
is no network. [WINDOWS.md](WINDOWS.md) is that laptop. Ollama on each machine
does every model job the OpenAI API used to do: answers, embeddings, and
reading scanned pages. Documents and questions stay on that machine.

Railway can keep serving until this Mac answers a real question correctly.
Then stop the Railway service. Do not copy its Chroma database over — those
vectors were made by `text-embedding-3-small` and this Mac's embedding model
cannot search them.

## One-time setup

On the Mac mini, from a checkout of this repo, while online:

```bash
git checkout main && git pull
chmod +x scripts/setup_mac_mini.sh
./scripts/setup_mac_mini.sh
```

The script reads the Mac's memory and pulls a chat model, a vision model, and
`nomic-embed-text`. Apple silicon uses Metal; there is nothing to configure
for a GPU. It writes `.env` from `.env.mac.example`.

| Unified memory | Chat | Scanned pages |
| --- | --- | --- |
| 8 GB | `qwen2.5vl:3b` (one model does both, and answers are weaker) | same model |
| 16 GB | `qwen2.5:7b-instruct` | `qwen2.5vl:3b` |
| 24 GB and up | `qwen2.5:14b-instruct` | `qwen2.5vl:7b` |

Force a model with `./scripts/setup_mac_mini.sh --model qwen2.5:14b-instruct`.
If Ollama is not installed and Homebrew is, the script installs it. Otherwise
install the Mac app from <https://ollama.com/download/mac>, open it once, and
run the script again. The Linux installer does not work on macOS.

## Rebuild the library

The search index has to be created here, from the files. Stop the server
first — Chroma cannot be written by two processes:

```bash
source .venv/bin/activate
python scripts/bulk_ingest.py "/path/to/your/documents"
```

That walks the folder, skips anything already indexed, and is safe to re-run
if it is interrupted. A couple of thousand PDFs takes hours on a 16 GB Mac
mini and less on a 32 GB one. It is a one-time cost for this embedding model.
Changing `OPENAI_EMBEDDING_MODEL` later means deleting `chroma_db_mac/` and
doing it again.

## Run it

```bash
./start.sh
```

On the Mac itself: <http://127.0.0.1:8000>

Suzie, on the same network, opens `http://<the-mini's-name>.local:8000`. The
name is printed when the server starts. Sign-in is still the roster in
`app/config.py`. The Mac has to stay awake (System Settings → Energy → prevent
automatic sleeping when the display is off, and enable wake for network
access).

Ollama listens only on this Mac. Leave it that way. Setting `OLLAMA_HOST` to
`0.0.0.0` would expose the model to the whole network with no sign-in, while
the app on port 8000 is the thing that checks the roster.

Do not forward port 8000 to the internet. Two people on the same network, or
on a VPN into that network, is the setup this is written for.

## What changed in the app

- `OPENAI_BASE_URL` points the existing client at Ollama. Chat and embeddings
  both use it. `OPENAI_API_KEY` must be non-empty; Ollama ignores the value,
  so `.env` sets it to `ollama`.
- `OPENAI_VISION_MODEL` is the model that reads scanned pages. A text model
  cannot, and leaving this blank (the Railway default) keeps vision on the
  chat model.
- `nomic-embed-text` is given the `search_document:` / `search_query:` prefixes
  it was trained with. Other embedding models are unchanged.
- `./start.sh` reads `HOST` and `PORT` from `.env`. The Mac config uses
  `0.0.0.0` so the second user can connect.

## After it works

Stop the Railway service. Keep its volume until you have asked something you
can check against a known document and the citation is right. Usage history
from Railway does not come along; the Mac starts a new `usage.db` under
`data/`.

## The Windows laptop

Once this Mac's index was built with `nomic-embed-text`, that index can be
copied onto the laptop. Both machines use the same embedding model and the
same prefixes. Stop the server on both sides and copy `chroma_db_mac/` to the
laptop as `chroma_db_laptop/`, and copy `data/` the same way. The steps are in
[WINDOWS.md](WINDOWS.md). Do not copy the Railway Chroma database to either
machine.
