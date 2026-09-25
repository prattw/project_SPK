#!/usr/bin/env bash
# Point the Mac mini's chat model at a different Ollama tag.
#
#   ./scripts/switch_local_model.sh qwen2.5:14b-instruct
#
# Does not change the embedding model. After documents are indexed, a
# different embedding model cannot search them.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <ollama-model-tag>" >&2
  echo "Example: $0 qwen2.5:14b-instruct" >&2
  exit 1
fi

MODEL="$1"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo ".env not found. Run the setup script for this machine first." >&2
  exit 1
fi
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed." >&2
  exit 1
fi

echo "-- Pulling $MODEL (skips the download if it is already on disk)..."
ollama pull "$MODEL"

if grep -q '^OPENAI_MODEL=' .env; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^OPENAI_MODEL=.*|OPENAI_MODEL=$MODEL|" .env
  else
    sed -i "s|^OPENAI_MODEL=.*|OPENAI_MODEL=$MODEL|" .env
  fi
else
  echo "OPENAI_MODEL=$MODEL" >> .env
fi

echo "-- .env updated: OPENAI_MODEL=$MODEL"
echo "-- Stop the app and start it again."
