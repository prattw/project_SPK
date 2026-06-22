#!/usr/bin/env bash
# Start Project SPK locally — then open http://127.0.0.1:8000
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and add your API keys before uploading or chatting."
  echo "The UI will still load without keys."
fi

echo "Starting server at http://127.0.0.1:8000"
echo "Keep this Terminal window open while you use the app."
exec .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
