#!/usr/bin/env bash
# Live terminal progress for a library ingest job.
#
# Usage:
#   export SPK_URL="https://projectspk-production.up.railway.app"
#   export SPK_TOKEN="your-bearer-token"
#   ./scripts/watch_library_ingest.sh 5743d9db-df04-46c3-860b-b56a00119b3c
#
# Optional: ./scripts/watch_library_ingest.sh JOB_ID 15   # poll every 15 seconds

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 JOB_ID [poll_interval_seconds]" >&2
  echo "Set SPK_URL and SPK_TOKEN first." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL="${2:-10}"

exec python3 "$SCRIPT_DIR/watch_library_ingest.py" "$1" --interval "$INTERVAL"
