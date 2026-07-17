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

# Shared email safety gate: bounce guardian, quiet hours, caps, and master kill,
# then the unified per-recipient gate (role-account, merged suppression/DNC
# incl. '@domain' entries, 60m recent-send cap). Exit 6 = recipient blocked.
# Exit 7 = per-minute throttle (transient — counter resets 60s after the last
# send; callers may stall + retry, per channel-limits.json per_minute semantics).
ROOT_DIR="$ROOT_DIR" TO_CHECK="$TO" python3 - <<'PYEOF'
import os
import sys

root = os.environ["ROOT_DIR"]
if root not in sys.path:
    sys.path.insert(0, root)

try:
    from runtime.db import connect
    from runtime.kill_switches import ChannelPaused, assert_channel_active, is_send_allowed
except Exception as exc:
    print(f"EMAIL SAFETY GATE UNAVAILABLE: {type(exc).__name__}: {exc}", file=sys.stderr)
    raise SystemExit(3)

conn = connect()
try:
    assert_channel_active(conn, "email")
except ChannelPaused as exc:
    if str(exc.reason).startswith("per-minute cap reached"):
        print(f"EMAIL THROTTLED: {exc.reason}", file=sys.stderr)
        raise SystemExit(7)
    print(f"EMAIL CHANNEL PAUSED: {exc.reason}", file=sys.stderr)
    raise SystemExit(4)
finally:
    conn.close()

# Per-recipient gate. cold=False: recipients on this path are opted-in
# subscribers / operator sends — the 7d cold frequency cap would break weekly
# newsletters. RICK_EMAIL_SEND_LIVE gates the drip path, NOT this
# operator-approved newsletter/broadcast path; force-pass ONLY that clause so
# the remaining checks still run (master kill is enforced by the gate above).
os.environ["RICK_EMAIL_SEND_LIVE"] = "1"
to = os.environ["TO_CHECK"].strip()
allowed, reason = is_send_allowed(to, cold=False)
if not allowed:
    print(f"SEND_BLOCKED reason={reason} to={to}", file=sys.stderr)
    raise SystemExit(6)
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
  # Unparseable response => EMPTY id, never '?': warmup dedupe collapses any
  # repeated non-empty id into one counted send, so '?' rows under-counted
  # the cap (the unsafe direction); empty ids count per-row.
  EMAIL_ID=$(echo "$BODY" | python3 -c "import json,sys; print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
  echo "✓ Sent to $TO (id: ${EMAIL_ID:-unparsed})"
  # Ledger visibility: typed row so warmup counters and cross-sender 60m dedup
  # see this volume. The row 'type' comes from RICK_LEDGER_TYPE (default
  # 'manual' — this is also the manual operator utility); newsletter-send.sh
  # exports 'newsletter', which day14-gate excludes from outreach counts.
  # The email is already delivered — an append failure must NOT flip
  # this send to FAIL (a re-run would double-send), so warn loud and exit 0.
  ROOT_DIR="$ROOT_DIR" TO="$TO" SUBJECT="$SUBJECT" EMAIL_ID="$EMAIL_ID" RICK_LEDGER_TYPE="${RICK_LEDGER_TYPE:-manual}" python3 - <<'PYEOF' || echo "WARNING: ledger append/record_send FAILED for $TO — email-sends.jsonl undercounts this send" >&2
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

root = os.environ["ROOT_DIR"]
if root not in sys.path:
    sys.path.insert(0, root)

ledger = Path(os.environ["RICK_DATA_ROOT"]) / "operations" / "email-sends.jsonl"
ledger.parent.mkdir(parents=True, exist_ok=True)
row = {
    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "to": os.environ["TO"],
    "subject": os.environ["SUBJECT"],
    "status": "sent",
    "type": os.environ["RICK_LEDGER_TYPE"],
    "resend_id": os.environ["EMAIL_ID"],
    "via": "resend-safe-send.sh",
}
with ledger.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")

from runtime.db import connect
from runtime.kill_switches import record_send

conn = connect()
try:
    record_send(conn, "email")
finally:
    conn.close()
PYEOF
else
  echo "✗ Failed: HTTP $HTTP_STATUS — $BODY" >&2
  exit 1
fi
