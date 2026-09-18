#!/usr/bin/env bash
# Swap the local prototype's chat model without re-running the full setup.
#
# Usage:
#   ./scripts/switch_local_model.sh qwen2.5:14b-instruct
#   ./scripts/switch_local_model.sh llama3.1:8b-instruct-q4_0
#
# Pulls the model with Ollama (if not already local) and updates OPENAI_MODEL
# in .env. Restart the app (./start.sh) afterward to pick it up.
#
# Tip: models that support OpenAI-style tool calling are needed for Agent
# mode (ENABLE_AGENT_MODE=true) — qwen2.5, llama3.1+, and mistral-nemo all
# support tools; check https://ollama.com/search?c=tools for the current list.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <ollama-model-tag>" >&2
  echo "Examples: qwen2.5:14b-instruct, qwen2.5:7b-instruct, llama3.1:8b-instruct-q4_0" >&2
  exit 1
fi

MODEL="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  echo ".env not found — run ./scripts/setup_local_prototype.sh first." >&2
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed — run ./scripts/setup_local_prototype.sh first." >&2
  exit 1
fi

echo "-- Pulling $MODEL (skips if already local)..."
ollama pull "$MODEL"

if grep -q '^OPENAI_MODEL=' .env; then
  sed -i "s/^OPENAI_MODEL=.*/OPENAI_MODEL=$MODEL/" .env
else
  echo "OPENAI_MODEL=$MODEL" >> .env
fi

echo "-- .env updated: OPENAI_MODEL=$MODEL"
echo "-- Restart the app to use it: stop ./start.sh (Ctrl+C) and run it again."
