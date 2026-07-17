#!/usr/bin/env bash
# Upload local library PDFs to the live Project SPK app (admin only).
#
# Prerequisites:
#   - curl (built into macOS)
#   - Your session token from the app (browser DevTools → Application → localStorage,
#     or sign in and copy the Authorization header from any API request)
#   - Your email must be in USAGE_ADMIN_EMAILS on Railway
#
# Usage:
#   export SPK_URL="https://YOUR-APP.up.railway.app"
#   export SPK_TOKEN="your-bearer-token"
#   ./scripts/upload_library_to_production.sh "/path/to/DOCUMENTS for RAG/New UFC docs for upload JUN26"
#   ./scripts/upload_library_to_production.sh "/path/to/DOCUMENTS for RAG/ARs"
#   ./scripts/upload_library_to_production.sh "/path/to/DOCUMENTS for RAG/DA Pams"
#
# Then start ingest (purges old UFC index entries first):
#   curl -s -X POST "$SPK_URL/admin/library/ingest" \
#     -H "Authorization: Bearer $SPK_TOKEN" \
#     -H "Content-Type: application/json" \
#     -d '{"purge_patterns":["UFC"]}'
#
# Poll progress (replace JOB_ID):
#   ./scripts/watch_library_ingest.sh JOB_ID
# Or raw JSON:
#   curl -s "$SPK_URL/jobs/JOB_ID" -H "Authorization: Bearer $SPK_TOKEN"

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/folder/with/pdfs" >&2
  exit 1
fi

: "${SPK_URL:?Set SPK_URL to your Railway app URL}"
: "${SPK_TOKEN:?Set SPK_TOKEN to your signed-in session token}"

ROOT="$1"
if [[ ! -d "$ROOT" ]]; then
  echo "Not a directory: $ROOT" >&2
  exit 1
fi

echo "Uploading PDFs from $ROOT to $SPK_URL/admin/library/upload ..."
count=0
while IFS= read -r f; do
  count=$((count + 1))
  echo "  → $(basename "$f")"
  curl -sf -X POST "$SPK_URL/admin/library/upload" \
    -H "Authorization: Bearer $SPK_TOKEN" \
    -F "file=@${f};filename=$(basename "$f")"
  echo
done < <(find "$ROOT" -type f \( -iname '*.pdf' \))

if [[ "$count" -eq 0 ]]; then
  echo "No PDF files found under $ROOT" >&2
  exit 1
fi

echo
echo "Done. Check queue:"
curl -s "$SPK_URL/admin/library/incoming" -H "Authorization: Bearer $SPK_TOKEN"
echo
echo "Start ingest with purge_patterns [\"UFC\"] when ready (see script header)."
