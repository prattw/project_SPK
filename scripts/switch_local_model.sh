#!/usr/bin/env bash
# Swap the local prototype's chat (or vision) model without re-running the
# full setup.
#
# Usage:
#   ./scripts/switch_local_model.sh qwen3:14b              # chat model
#   ./scripts/switch_local_model.sh --vision qwen2.5vl:7b  # vision/OCR model
#   ./scripts/switch_local_model.sh llama3.1:8b-instruct-q4_0
#
# Pulls the model with Ollama (if not already local) and updates OPENAI_MODEL
# (or OPENAI_VISION_MODEL with --vision) in .env. Restart the app
# (./start.sh) afterward to pick it up.
#
# Tip: models that support OpenAI-style tool calling are needed for Agent
# mode (ENABLE_AGENT_MODE=true) — qwen3, llama3.1+, and mistral-nemo all
# support tools; check https://ollama.com/search?c=tools for the current list.
#
# Tip: OPENAI_VISION_MODEL only matters for scanned-PDF OCR and image
# uploads — it does NOT need to be the same model as everyday chat, and most
# Ollama chat models (qwen2.5, qwen3, llama3.1+) have no image understanding
# at all, so OCR silently produces nothing unless this is set to a model
# that actually supports vision; check https://ollama.com/search?c=vision
# for the current list (llava and qwen2.5vl are both solid, widely-available
# choices).

set -euo pipefail

VAR_NAME="OPENAI_MODEL"
if [[ "${1:-}" == "--vision" ]]; then
  VAR_NAME="OPENAI_VISION_MODEL"
  shift
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 [--vision] <ollama-model-tag>" >&2
  echo "Examples: qwen3:14b, qwen3:8b, llama3.1:8b-instruct-q4_0" >&2
  echo "          --vision qwen2.5vl:7b, --vision llava:7b" >&2
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

if grep -q "^${VAR_NAME}=" .env; then
  sed -i "s|^${VAR_NAME}=.*|${VAR_NAME}=${MODEL}|" .env
else
  echo "${VAR_NAME}=${MODEL}" >> .env
fi

echo "-- .env updated: ${VAR_NAME}=${MODEL}"
echo "-- Restart the app to use it: stop ./start.sh (Ctrl+C) and run it again."
