#!/usr/bin/env bash
# Start Review Desk on this Mac only. Does not start Project SPK.
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "Run ./drchecks/setup_mac.sh once, while online." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "Copy drchecks/.env.example to drchecks/.env first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
set -a
# shellcheck disable=SC1091
source .env
set +a

HOST_ADDR="${DRCHECKS_HOST:-127.0.0.1}"
PORT_NUM="${DRCHECKS_PORT:-8010}"
if [[ "$HOST_ADDR" != "127.0.0.1" && "$HOST_ADDR" != "localhost" ]]; then
  echo "Review Desk stays on this Mac. DRCHECKS_HOST must be 127.0.0.1." >&2
  exit 1
fi

echo "Review Desk at http://${HOST_ADDR}:${PORT_NUM}"
echo "Project SPK is a different app and is not started here."
exec python -m uvicorn server:app --host "$HOST_ADDR" --port "$PORT_NUM"
