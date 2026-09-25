#!/usr/bin/env bash
# One-time setup: run Project SPK on this Mac mini, with Ollama instead of
# the OpenAI API. See MAC_MINI.md.
#
# Run once, while online. It installs Ollama if Homebrew can, picks a chat
# model and a vision model from the Mac's unified memory, pulls them plus
# the embedding model, and writes .env.
#
#   ./scripts/setup_mac_mini.sh
#   ./scripts/setup_mac_mini.sh --model qwen2.5:14b-instruct

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This setup is for a Mac. It detects Apple unified memory, which Linux cannot." >&2
  exit 1
fi

FORCE_MODEL=""
if [[ "${1:-}" == "--model" && -n "${2:-}" ]]; then
  FORCE_MODEL="$2"
fi

EMBED_MODEL="nomic-embed-text"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== Project SPK on this Mac mini =="

MEM_BYTES="$(sysctl -n hw.memsize)"
TOTAL_RAM_GB="$(( MEM_BYTES / 1024 / 1024 / 1024 ))"
CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple silicon")"
echo "-- ${CHIP}, ${TOTAL_RAM_GB} GB unified memory"
echo "   On a Mac that memory is the GPU memory. Ollama uses Metal on its own."

# Leave room for macOS, Chroma, and the embedding model. The vision model is
# loaded only while a scanned page is being read, then unloaded.
if [[ -n "$FORCE_MODEL" ]]; then
  CHAT_MODEL="$FORCE_MODEL"
  VISION_MODEL="qwen2.5vl:7b"
elif (( TOTAL_RAM_GB >= 32 )); then
  CHAT_MODEL="qwen2.5:14b-instruct"
  VISION_MODEL="qwen2.5vl:7b"
elif (( TOTAL_RAM_GB >= 24 )); then
  CHAT_MODEL="qwen2.5:14b-instruct"
  VISION_MODEL="qwen2.5vl:7b"
elif (( TOTAL_RAM_GB >= 16 )); then
  CHAT_MODEL="qwen2.5:7b-instruct"
  VISION_MODEL="qwen2.5vl:3b"
else
  # 8 GB cannot hold a 7B model and macOS at once. One small vision model
  # does both jobs, less well.
  CHAT_MODEL="qwen2.5vl:3b"
  VISION_MODEL="qwen2.5vl:3b"
fi

CONTEXT_CHARS=40000
if (( TOTAL_RAM_GB >= 32 )); then
  CONTEXT_CHARS=80000
fi

echo "-- Chat model:      $CHAT_MODEL"
echo "-- Vision model:    $VISION_MODEL"
echo "-- Embedding model: $EMBED_MODEL"
echo

if ! command -v ollama >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "-- Installing Ollama with Homebrew..."
    brew install ollama
  else
    echo "Ollama is not installed, and Homebrew is not either." >&2
    echo "Install the Mac app from https://ollama.com/download/mac, open it once," >&2
    echo "then run this script again. The Linux install script does not work on macOS." >&2
    exit 1
  fi
else
  echo "-- Ollama already installed."
fi

echo "-- Starting Ollama..."
if command -v brew >/dev/null 2>&1 && brew services list 2>/dev/null | grep -q '^ollama'; then
  brew services start ollama >/dev/null 2>&1 || true
fi
if ! curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  open -a Ollama >/dev/null 2>&1 || true
  (ollama serve >/tmp/ollama.log 2>&1 &) || true
fi

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != "1" ]]; then
  echo "Ollama did not start. Open the Ollama app from Applications and run this again." >&2
  exit 1
fi

echo "-- Pulling chat model ($CHAT_MODEL). The first download is several GB."
ollama pull "$CHAT_MODEL"
if [[ "$VISION_MODEL" != "$CHAT_MODEL" ]]; then
  echo "-- Pulling vision model ($VISION_MODEL)..."
  ollama pull "$VISION_MODEL"
fi
echo "-- Pulling embedding model ($EMBED_MODEL)..."
ollama pull "$EMBED_MODEL"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found. Install it with: brew install python" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "-- Creating Python virtual environment..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

replace_env() {
  local key="$1" value="$2"
  # BSD sed (the one macOS ships) needs the empty backup suffix.
  sed -i '' "s|^${key}=.*|${key}=${value}|" .env
}

if [[ ! -f .env ]]; then
  cp .env.mac.example .env
  replace_env OPENAI_MODEL "$CHAT_MODEL"
  replace_env OPENAI_VISION_MODEL "$VISION_MODEL"
  replace_env OPENAI_EMBEDDING_MODEL "$EMBED_MODEL"
  replace_env MAX_CONTEXT_CHARS "$CONTEXT_CHARS"
  secret="$(openssl rand -hex 32)"
  replace_env AUTH_SECRET "$secret"
  echo "-- Wrote .env for this Mac."
else
  echo "-- .env already exists, so it was left as-is."
  echo "   If it still points at OpenAI, move it aside and run this script again."
fi

cat <<EOF

== This Mac is ready ==

Chat, embeddings, and scanned-page reading all go to Ollama on this machine.
Nothing is sent to OpenAI.

  1. ./start.sh
  2. Open http://127.0.0.1:8000 and sign in.
  3. Rebuild the search index from your documents. The Railway index cannot
     be copied — it was embedded with a different model. With the server
     stopped:

       source .venv/bin/activate
       python scripts/bulk_ingest.py "/path/to/your/documents"

The other roster user, on the same network, opens
http://$(hostname -s).local:8000
Ollama itself stays on this Mac only.

Leave the Mac awake while people are using it (System Settings → Energy →
prevent automatic sleeping). Leave Railway running until you decide to
switch, and only after a question you know the answer to comes back with
the right citations.
EOF
