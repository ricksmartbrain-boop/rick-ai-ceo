#!/usr/bin/env bash
# resend-safe-send.sh — SAFE Resend API wrapper
#
# PRIVACY RULE (non-negotiable):
#   Never put more than ONE subscriber email in the "to" field.
#   This script enforces it and will refuse to send if violated.
#
# Usage:
#   resend-safe-send.sh --to "email@example.com" --subject "Subject" --html "<p>Body</p>"
#   resend-safe-send.sh --to "email@example.com" --subject "Subject" --html-file body.html

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source ~/clawd/config/rick.env 2>/dev/null || true
export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

RESEND_API_KEY="${RESEND_API_KEY:?ERROR: RESEND_API_KEY environment variable is not set}"
FROM_EMAIL="Rick <rick@meetrick.ai>"
TO=""
SUBJECT=""
HTML=""
HTML_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --to) TO="$2"; shift 2 ;;
    --subject) SUBJECT="$2"; shift 2 ;;
    --html) HTML="$2"; shift 2 ;;
    --html-file) HTML_FILE="$2"; shift 2 ;;
    --from) FROM_EMAIL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

# ── Guard: exactly one recipient ───────────────────────────────────────────────
if [[ -z "$TO" ]]; then
  echo "ERROR: --to is required" >&2
  exit 1
fi

# Count commas or semicolons — multiple emails = hard fail
COMMA_COUNT=$(echo "$TO" | tr -cd ',' | wc -c)
SEMI_COUNT=$(echo "$TO" | tr -cd ';' | wc -c)
AT_COUNT=$(echo "$TO" | tr -cd '@' | wc -c)

if [[ "$COMMA_COUNT" -gt 0 ]] || [[ "$SEMI_COUNT" -gt 0 ]] || [[ "$AT_COUNT" -gt 1 ]]; then
  echo "PRIVACY VIOLATION BLOCKED: Multiple emails detected in --to field." >&2
  echo "  Value: $TO" >&2
  echo "  Rule: Send one email per recipient. Loop externally." >&2
  exit 2
fi

# Shared email safety gate: bounce guardian, quiet hours, caps, and master kill.
ROOT_DIR="$ROOT_DIR" python3 - <<'PYEOF'
import os
import sys

root = os.environ["ROOT_DIR"]
if root not in sys.path:
    sys.path.insert(0, root)

try:
    from runtime.db import connect
    from runtime.kill_switches import ChannelPaused, assert_channel_active
except Exception as exc:
    print(f"EMAIL SAFETY GATE UNAVAILABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(3)

conn = connect()
try:
    assert_channel_active(conn, "email")
except ChannelPaused as exc:
    print(f"EMAIL CHANNEL PAUSED: {exc.reason}", file=sys.stderr)
    raise SystemExit(4)
finally:
    conn.close()
PYEOF

# Local suppression gate: never send to bounced/unsubscribed addresses.
TO_CHECK="$TO" SUPPRESSION_FILE="$RICK_DATA_ROOT/mailbox/suppression.txt" python3 - <<'PYEOF'
import os
from pathlib import Path

target = os.environ["TO_CHECK"].strip().lower()
path = Path(os.environ["SUPPRESSION_FILE"])
if not path.exists():
    raise SystemExit(0)

for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = raw.split("#", 1)[0].strip().lower()
    if line and line == target:
        print(f"SUPPRESSION VIOLATION BLOCKED: {target}", file=__import__("sys").stderr)
        raise SystemExit(5)
PYEOF

# ── Resolve HTML body ──────────────────────────────────────────────────────────
if [[ -z "$HTML" ]] && [[ -n "$HTML_FILE" ]]; then
  if [[ ! -f "$HTML_FILE" ]]; then
    echo "ERROR: HTML file not found: $HTML_FILE" >&2
    exit 1
  fi
  HTML=$(cat "$HTML_FILE")
fi

if [[ -z "$HTML" ]]; then
  echo "ERROR: --html or --html-file is required" >&2
  exit 1
fi

if [[ -z "$SUBJECT" ]]; then
  echo "ERROR: --subject is required" >&2
  exit 1
fi

# ── Build payload ──────────────────────────────────────────────────────────────
PAYLOAD=$(FROM_EMAIL="$FROM_EMAIL" TO="$TO" SUBJECT="$SUBJECT" HTML="$HTML" python3 - <<'PYEOF'
import json, os
payload = {
    "from": os.environ["FROM_EMAIL"],
    "to": [os.environ["TO"]],
    "subject": os.environ["SUBJECT"],
    "html": os.environ["HTML"],
}
print(json.dumps(payload))
PYEOF
)

# ── Send ───────────────────────────────────────────────────────────────────────
TMP_RESPONSE="$(mktemp "${TMPDIR:-/tmp}/resend-response.XXXXXX.json")"
trap 'rm -f "$TMP_RESPONSE"' EXIT

HTTP_STATUS=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  -o "$TMP_RESPONSE" \
  -w "%{http_code}")

BODY=$(cat "$TMP_RESPONSE" 2>/dev/null || echo "")

if [[ "$HTTP_STATUS" == "200" ]] || [[ "$HTTP_STATUS" == "201" ]]; then
  EMAIL_ID=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id','?'))" 2>/dev/null || echo "?")
  echo "✓ Sent to $TO (id: $EMAIL_ID)"
else
  echo "✗ Failed: HTTP $HTTP_STATUS — $BODY" >&2
  exit 1
fi
