#!/usr/bin/env bash
# One-time setup for Review Desk on the Mac mini. Project SPK is unchanged.
# Run while online, from anywhere:
#   ./drchecks/setup_mac.sh

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This setup is for the Mac mini." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "== Review Desk on this Mac =="

MEM_BYTES="$(sysctl -n hw.memsize)"
TOTAL_RAM_GB="$(( MEM_BYTES / 1024 / 1024 / 1024 ))"
if (( TOTAL_RAM_GB >= 16 )); then
  MODEL="qwen2.5:7b-instruct"
else
  MODEL="qwen2.5:3b"
fi
echo "-- ${TOTAL_RAM_GB} GB unified memory, chat model ${MODEL}"

if ! command -v ollama >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install ollama
  else
    echo "Install Ollama from https://ollama.com/download/mac, open it once, and run this again." >&2
    exit 1
  fi
fi

if command -v brew >/dev/null 2>&1; then
  brew services start ollama >/dev/null 2>&1 || true
fi
if ! curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  open -a Ollama >/dev/null 2>&1 || true
  (ollama serve >/tmp/review-desk-ollama.log 2>&1 &) || true
fi
ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != "1" ]]; then
  echo "Ollama did not start. Open the Ollama app and run this again." >&2
  exit 1
fi

echo "-- Pulling ${MODEL}"
ollama pull "$MODEL"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  sed -i '' "s|^DRCHECKS_MODEL=.*|DRCHECKS_MODEL=${MODEL}|" .env
  echo "-- Wrote drchecks/.env"
else
  echo "-- drchecks/.env already exists, left as-is."
fi

cat <<EOF

== Review Desk is ready ==

This does not change Project SPK on Railway.

  ./drchecks/start.sh
  Open http://127.0.0.1:8010

Import a ProjNet XML export, or start from notes. Paste the draft back
into ProjNet. Leave Ollama on localhost.
EOF
