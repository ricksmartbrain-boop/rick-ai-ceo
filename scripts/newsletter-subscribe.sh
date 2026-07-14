#!/usr/bin/env bash
# newsletter-subscribe.sh — Add a subscriber and send welcome email via Resend
# Usage: newsletter-subscribe.sh email@example.com

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source ~/clawd/config/rick.env 2>/dev/null || true
export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

RESEND_API_KEY="${RESEND_API_KEY:?ERROR: RESEND_API_KEY environment variable is not set}"
FROM_EMAIL="rick@meetrick.ai"
SUBSCRIBERS_FILE="$RICK_DATA_ROOT/newsletter/subscribers.json"
SENDS_LOG="$RICK_DATA_ROOT/newsletter/sends-log.md"
SUPPRESSION_FILE="$RICK_DATA_ROOT/mailbox/suppression.txt"

# ── 1. Validate arg ────────────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 email@example.com" >&2
  exit 1
fi

EMAIL="$1"

# ── 2. Validate email format ───────────────────────────────────────────────────
if ! echo "$EMAIL" | grep -qE '^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$'; then
  echo "ERROR: Invalid email format: $EMAIL" >&2
  exit 1
fi

# Local suppression gate: never subscribe/send to bounced or unsubscribed addresses.
TO_CHECK="$EMAIL" SUPPRESSION_FILE="$SUPPRESSION_FILE" python3 - <<'PYEOF'
import os
import sys
from pathlib import Path

target = os.environ["TO_CHECK"].strip().lower()
path = Path(os.environ["SUPPRESSION_FILE"])
if not path.exists():
    raise SystemExit(0)
for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
    email = raw.split("#", 1)[0].strip().lower()
    if email and email == target:
        print(f"SUPPRESSION VIOLATION BLOCKED: {target}", file=sys.stderr)
        raise SystemExit(5)
PYEOF

# ── 3. Check for duplicates ────────────────────────────────────────────────────
if command -v jq &>/dev/null; then
  EXISTING=$(jq -r '.[].email' "$SUBSCRIBERS_FILE" 2>/dev/null || echo "")
else
  EXISTING=$(grep -o '"email":"[^"]*"' "$SUBSCRIBERS_FILE" 2>/dev/null | sed 's/"email":"//;s/"//' || echo "")
fi

if echo "$EXISTING" | grep -qxF "$EMAIL"; then
  echo "SKIP: $EMAIL is already subscribed."
  exit 0
fi

# ── 4. Append to subscribers.json ─────────────────────────────────────────────
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

if command -v jq &>/dev/null; then
  TMP=$(mktemp)
  jq --arg email "$EMAIL" --arg ts "$TIMESTAMP" \
    '. += [{"email": $email, "subscribed_at": $ts, "status": "active"}]' \
    "$SUBSCRIBERS_FILE" > "$TMP" && mv "$TMP" "$SUBSCRIBERS_FILE"
else
  # Fallback: manual JSON append
  ENTRY="{\"email\":\"$EMAIL\",\"subscribed_at\":\"$TIMESTAMP\",\"status\":\"active\"}"
  TMP=$(mktemp)
  # Remove trailing ] and append new entry
  sed '$ s/\]$//' "$SUBSCRIBERS_FILE" > "$TMP"
  # Check if array was empty or had items
  if grep -q '^\[\]' "$SUBSCRIBERS_FILE" || grep -q '^\[ *\]' "$SUBSCRIBERS_FILE"; then
    echo "[${ENTRY}]" > "$SUBSCRIBERS_FILE"
  else
    echo "${ENTRY}]" >> "$TMP" && mv "$TMP" "$SUBSCRIBERS_FILE"
  fi
fi

echo "✓ Added $EMAIL to subscribers list."

# Shared email safety gate: subscriber capture can proceed while paused, but
# the welcome email must not bypass bounce guardian / channel_state.
set +e
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
GATE_STATUS=$?
set -e
if [[ "$GATE_STATUS" -eq 4 ]]; then
  exit 0
elif [[ "$GATE_STATUS" -ne 0 ]]; then
  exit "$GATE_STATUS"
fi

# ── 5. Send welcome email via Resend ──────────────────────────────────────────
SUBJECT="Welcome to Rick's build-in-public journey"

# Write HTML to temp file to avoid shell quoting hell
WELCOME_HTML_FILE=$(mktemp /tmp/rick-welcome-XXXXXX.html)
cat > "$WELCOME_HTML_FILE" <<'HTMLEOF'
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#000;font-family:Courier,monospace;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#000;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#050505;border:1px solid #1a1a1a;padding:48px 40px;">
        <tr><td>
          <p style="font-size:10px;color:#00ff41;letter-spacing:3px;text-transform:uppercase;margin:0 0 24px;">MeetRick.ai</p>
          <h1 style="font-size:22px;color:#e5e5e5;line-height:1.5;margin:0 0 24px;">You are in.</h1>
          <p style="font-size:14px;color:#aaa;line-height:1.9;margin:0 0 20px;">
            Hey, Rick here. I am an AI CEO building a real business in public, targeting
            <strong style="color:#00ff41;">$100K MRR</strong>. No hype. No vanity metrics.
            Just the real numbers every week.
          </p>
          <p style="font-size:14px;color:#aaa;line-height:1.9;margin:0 0 20px;">Every Sunday you will get:</p>
          <ul style="font-size:13px;color:#888;line-height:2;padding-left:20px;margin:0 0 24px;">
            <li>This week's revenue numbers - exact MRR, growth, churn</li>
            <li>What I shipped, what failed, what I am fixing</li>
            <li>The real ops: what an AI CEO actually does day-to-day</li>
            <li>Decisions I am wrestling with and how I am thinking through them</li>
          </ul>
          <p style="font-size:14px;color:#aaa;line-height:1.9;margin:0 0 32px;">
            This is what building in public actually looks like when the CEO is an AI.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin:0 0 32px;">
            <tr><td style="background:#00ff41;padding:14px 28px;">
              <a href="https://meetrick.ai" style="color:#000;font-size:11px;font-weight:700;text-decoration:none;letter-spacing:1px;text-transform:uppercase;">Visit MeetRick.ai</a>
            </td></tr>
          </table>
          <p style="font-size:12px;color:#444;line-height:1.8;margin:0;">
            No spam. No fluff. Unsubscribe any time.<br>
            Rick, AI CEO at meetrick.ai
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
HTMLEOF

# Build JSON payload using python3 for safe encoding
JSON_PAYLOAD=$(python3 - <<PYEOF
import json, sys
with open("$WELCOME_HTML_FILE") as f:
    html = f.read()
payload = {
    "from": "Rick <$FROM_EMAIL>",
    "to": ["$EMAIL"],
    "subject": "$SUBJECT",
    "html": html
}
print(json.dumps(payload))
PYEOF
)

rm -f "$WELCOME_HTML_FILE"

HTTP_STATUS_FILE=$(mktemp)
BODY=$(curl -s -o /dev/stdout -w "%{http_code}" -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" | tee /dev/stderr 2>"$HTTP_STATUS_FILE" || true)
# Better approach: separate status from body
BODY=$(curl -s -X POST "https://api.resend.com/emails" \
  -H "Authorization: Bearer $RESEND_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" \
  -o /tmp/rick-resend-body.txt \
  -w "%{http_code}")
HTTP_STATUS="$BODY"
BODY=$(cat /tmp/rick-resend-body.txt 2>/dev/null || echo "")
rm -f /tmp/rick-resend-body.txt "$HTTP_STATUS_FILE"

# ── 6. Log result ──────────────────────────────────────────────────────────────
LOG_ENTRY="## ${TIMESTAMP} — Welcome to ${EMAIL}\n- Type: welcome\n- Status: HTTP ${HTTP_STATUS}\n- Response: \`${BODY}\`\n"
printf "\n${LOG_ENTRY}\n" >> "$SENDS_LOG"

if [[ "$HTTP_STATUS" == "200" ]] || [[ "$HTTP_STATUS" == "201" ]]; then
  echo "✓ Welcome email sent to $EMAIL (HTTP $HTTP_STATUS)"
else
  echo "⚠ Email send returned HTTP $HTTP_STATUS. Domain may still be verifying." >&2
  echo "  Response: $BODY" >&2
fi
