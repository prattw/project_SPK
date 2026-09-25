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
  echo "On a Mac mini running Ollama, use .env.mac.example instead. See MAC_MINI.md."
  echo "The UI will still load without keys."
fi

# HOST and PORT come from .env so a Mac mini can be reached by the other
# roster user on the same network. Defaults stay on this machine only.
HOST_ADDR="127.0.0.1"
PORT_NUM="8000"
if [[ -f .env ]]; then
  env_host="$(grep -E '^HOST=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true)"
  env_port="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2- | tr -d '"' || true)"
  [[ -n "$env_host" ]] && HOST_ADDR="$env_host"
  [[ -n "$env_port" ]] && PORT_NUM="$env_port"
fi

echo "Starting server at http://${HOST_ADDR}:${PORT_NUM}"
if [[ "$HOST_ADDR" == "0.0.0.0" ]]; then
  echo "Listening on every interface. Other people on this network can open http://$(hostname -s 2>/dev/null || echo this-mac).local:${PORT_NUM}"
fi
echo "Keep this Terminal window open while you use the app."
# Exclude upload/index paths from --reload so saving a file does not restart mid-upload.
exec .venv/bin/uvicorn app.main:app --reload --host "$HOST_ADDR" --port "$PORT_NUM" \
  --reload-exclude 'data/*' \
  --reload-exclude 'chroma_db/*' \
  --reload-exclude 'chroma_db_mac/*' \
  --reload-exclude 'auth.db'
