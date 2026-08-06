#!/usr/bin/env bash
# One-time setup for the offline/austere deployment kit.
#
# Run this ONCE, on the actual deployment laptop, WHILE STILL ON A NETWORK.
# It installs Ollama, pulls the local chat + embedding models, and installs
# the Python dependencies for Project SPK — everything needed to run fully
# offline afterward. See DEPLOY_OFFLINE.md for the full picture.
#
# Usage:
#   ./scripts/setup_offline_macbook.sh              # 8 GB RAM default (llama3.2:3b)
#   ./scripts/setup_offline_macbook.sh --16gb        # 16 GB RAM (qwen2.5:7b)

set -euo pipefail

CHAT_MODEL="llama3.2:3b"
if [[ "${1:-}" == "--16gb" ]]; then
  CHAT_MODEL="qwen2.5:7b"
fi
EMBED_MODEL="nomic-embed-text"

echo "== Project SPK offline setup =="
echo "Chat model:      $CHAT_MODEL"
echo "Embedding model: $EMBED_MODEL"
echo

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew not found. Install it first: https://brew.sh" >&2
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "-- Installing Ollama via Homebrew..."
  brew install ollama
else
  echo "-- Ollama already installed."
fi

echo "-- Starting Ollama service..."
brew services start ollama || true
sleep 2

echo "-- Pulling chat model ($CHAT_MODEL)... this can take a while on the first run."
ollama pull "$CHAT_MODEL"

echo "-- Pulling embedding model ($EMBED_MODEL)..."
ollama pull "$EMBED_MODEL"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install Python 3.12+ first." >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "-- Creating Python virtual environment..."
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "-- Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

if [[ ! -f .env ]]; then
  echo "-- Writing .env for offline/Ollama use..."
  cp .env.offline.example .env
  sed -i '' "s/^OPENAI_MODEL=.*/OPENAI_MODEL=$CHAT_MODEL/" .env
  sed -i '' "s/^OPENAI_EMBEDDING_MODEL=.*/OPENAI_EMBEDDING_MODEL=$EMBED_MODEL/" .env
  echo "   Wrote .env — review it before running the app."
else
  echo "-- .env already exists, leaving it alone."
  echo "   Make sure it matches .env.offline.example (OPENAI_BASE_URL, model names)."
fi

cat <<EOF

== Setup complete ==

Next steps:
  1. Copy a pre-built chroma_db/ and data/ folder onto this laptop
     (the document library snapshot — see DEPLOY_OFFLINE.md).
  2. Test it now, while still on the network, so problems surface before
     departure:
       source .venv/bin/activate
       ./start.sh
     Then open http://127.0.0.1:8000 in a browser.
  3. Once confirmed working, this laptop no longer needs any network
     connection to run Project SPK.
EOF
