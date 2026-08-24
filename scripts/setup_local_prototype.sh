#!/usr/bin/env bash
# One-time setup for a personal local prototype of Project SPK — for testing
# the self-hosted (Ollama-backed) architecture on your own laptop while
# production keeps running on Railway with the OpenAI API, untouched.
#
# Targets Linux (e.g. a Lenovo laptop running Ubuntu/Fedora/etc). If your
# laptop runs Windows, do this inside WSL2 — see LOCAL_PROTOTYPE.md.
#
# Run this ONCE, on a network connection. It installs Ollama, auto-picks a
# chat model sized to your RAM (and GPU, if an NVIDIA card is detected),
# pulls it, installs Python deps, and writes .env from .env.local.example.
#
# Usage:
#   ./scripts/setup_local_prototype.sh              # auto-detect model tier
#   ./scripts/setup_local_prototype.sh --model qwen2.5:14b-instruct  # force a model

set -euo pipefail

FORCE_MODEL=""
if [[ "${1:-}" == "--model" && -n "${2:-}" ]]; then
  FORCE_MODEL="$2"
fi

EMBED_MODEL="nomic-embed-text"

echo "== Project SPK local prototype setup =="

# --- Detect available RAM (Linux) ---
TOTAL_RAM_GB=8
if [[ -r /proc/meminfo ]]; then
  TOTAL_RAM_KB="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
  TOTAL_RAM_GB=$(( TOTAL_RAM_KB / 1024 / 1024 ))
fi
echo "-- Detected ~${TOTAL_RAM_GB} GB system RAM"

# --- Detect an NVIDIA GPU, if present (some Lenovo ThinkPad P-series have one) ---
GPU_VRAM_GB=0
if command -v nvidia-smi >/dev/null 2>&1; then
  GPU_VRAM_MB="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 || true)"
  if [[ -n "${GPU_VRAM_MB:-}" ]]; then
    GPU_VRAM_GB=$(( GPU_VRAM_MB / 1024 ))
    echo "-- Detected NVIDIA GPU with ~${GPU_VRAM_GB} GB VRAM (Ollama will use it automatically)"
  fi
else
  echo "-- No NVIDIA GPU detected — this will run on CPU. Expect it to be slow;"
  echo "   that's expected for a prototype box, not a verdict on the software."
fi

# --- Pick a model tier ---
# GPU VRAM takes priority over system RAM when a discrete NVIDIA GPU is
# present: a model that overflows VRAM falls back to slow CPU offload, which
# defeats the point of having a GPU at all. An 8GB card (e.g. a laptop RTX
# 5050/4060) fits a 7-8B model at Q4 with headroom for context; it does NOT
# comfortably fit a 14B model, even though 32GB of system RAM alone would
# otherwise qualify for that tier below.
if [[ -n "$FORCE_MODEL" ]]; then
  CHAT_MODEL="$FORCE_MODEL"
elif (( GPU_VRAM_GB >= 20 )); then
  CHAT_MODEL="qwen2.5:14b-instruct"
elif (( GPU_VRAM_GB >= 12 )); then
  CHAT_MODEL="qwen2.5:14b-instruct"
elif (( GPU_VRAM_GB >= 6 )); then
  CHAT_MODEL="qwen2.5:7b-instruct"
elif (( TOTAL_RAM_GB >= 32 )); then
  CHAT_MODEL="qwen2.5:14b-instruct"
elif (( TOTAL_RAM_GB >= 16 )); then
  CHAT_MODEL="qwen2.5:7b-instruct"
else
  CHAT_MODEL="llama3.2:3b"
fi

echo "-- Chat model:      $CHAT_MODEL"
echo "-- Embedding model: $EMBED_MODEL"
echo

# --- Install Ollama ---
if ! command -v ollama >/dev/null 2>&1; then
  echo "-- Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "-- Ollama already installed."
fi

echo "-- Starting Ollama service..."
if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q '^ollama\.service'; then
  sudo systemctl enable --now ollama || true
else
  (ollama serve >/tmp/ollama.log 2>&1 &) || true
fi
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
  echo "-- Writing .env for the local prototype (Ollama-backed)..."
  cp .env.local.example .env
  sed -i "s/^OPENAI_MODEL=.*/OPENAI_MODEL=$CHAT_MODEL/" .env
  sed -i "s/^OPENAI_EMBEDDING_MODEL=.*/OPENAI_EMBEDDING_MODEL=$EMBED_MODEL/" .env
  echo "   Wrote .env — review it before running the app."
else
  echo "-- .env already exists, leaving it alone."
  echo "   Compare it against .env.local.example if something looks off."
fi

cat <<EOF

== Setup complete ==

This laptop now has its own local Project SPK prototype, fully separate
from production (Railway + OpenAI, unaffected by any of this).

Next steps:
  1. source .venv/bin/activate
  2. ./start.sh
  3. Open http://127.0.0.1:8000 in a browser and upload a test document.

Notes:
  - The vector index this creates (chroma_db_local/) uses Ollama embeddings,
    which are NOT compatible with production's OpenAI embeddings — don't mix
    the two chroma_db directories.
  - If answers feel slow or shallow, that's expected on laptop hardware —
    see LOCAL_PROTOTYPE.md for what to expect and how this maps to the
    RTX 5090 / PRO 6000 box you're planning to buy.
EOF
