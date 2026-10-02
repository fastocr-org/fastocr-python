#!/usr/bin/env bash
# FastOCR Bash Recipe: Extract raw text from a scanned PDF without SDK dependencies.
set -euo pipefail

FILE="${1:-scan.pdf}"
API_KEY="${FASTOCR_API_KEY:-}"

if [[ -z "$API_KEY" ]]; then
  echo "Error: FASTOCR_API_KEY environment variable is not set." >&2
  exit 1
fi

if [[ ! -f "$FILE" ]]; then
  echo "Error: File '$FILE' not found." >&2
  exit 1
fi

SIZE=$(wc -c < "$FILE" | tr -d ' ')
FILENAME=$(basename "$FILE")
IDEMPOTENCY_KEY=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)

echo "1. Creating document job..."
CREATE_RESP=$(curl -s -X POST "https://api.fastocr.org/v1/documents" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: $IDEMPOTENCY_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"$FILENAME\",\"size_bytes\":$SIZE}")

DOC_ID=$(echo "$CREATE_RESP" | grep -o '"id":"[^"]*' | cut -d'"' -f4)
UPLOAD_URL=$(echo "$CREATE_RESP" | grep -o '"upload_url":"[^"]*' | cut -d'"' -f4)

if [[ -z "$DOC_ID" || -z "$UPLOAD_URL" ]]; then
  echo "Error: could not create the document: $CREATE_RESP" >&2
  exit 1
fi

echo "2. Uploading file to S3 ($DOC_ID)..."
curl -s -X PUT "$UPLOAD_URL" \
  -H "Content-Type: application/pdf" \
  -T "$FILE"

echo "3. Starting processing..."
curl -s -X POST "https://api.fastocr.org/v1/documents/$DOC_ID/start" \
  -H "Authorization: Bearer $API_KEY" > /dev/null

echo "4. Polling for completion..."
while true; do
  STATUS_RESP=$(curl -s "https://api.fastocr.org/v1/documents/$DOC_ID" \
    -H "Authorization: Bearer $API_KEY")
  STATUS=$(echo "$STATUS_RESP" | grep -o '"status":"[^"]*' | cut -d'"' -f4)

  if [[ "$STATUS" == "completed" ]]; then
    echo "Processing complete!"
    break
  elif [[ "$STATUS" == "partial" ]]; then
    echo "Warning: the account ran out of pages; only part of the document was processed." >&2
    break
  elif [[ "$STATUS" == "failed" ]]; then
    echo "Processing failed: $STATUS_RESP" >&2
    exit 1
  fi
  sleep 2
done

echo "5. Fetching raw extracted text..."
OUTPUT_RESP=$(curl -s "https://api.fastocr.org/v1/documents/$DOC_ID/output?format=text" \
  -H "Authorization: Bearer $API_KEY")
TEXT_URL=$(echo "$OUTPUT_RESP" | grep -o '"url":"[^"]*' | cut -d'"' -f4)

curl -s "$TEXT_URL"
