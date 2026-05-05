#!/usr/bin/env bash
# newsletter-send.sh — Send a newsletter to all subscribers via Resend
#
# CRITICAL RULE: ALWAYS send one email per subscriber. NEVER put multiple
# subscriber emails in a single "to" array — this exposes all subscribers
# to each other. This script loops and sends individually. Do not change this.
#
# Usage options:
#   newsletter-send.sh "Subject line" "path/to/body.html"
#   newsletter-send.sh "Subject line"            # reads ~/rick-vault/newsletter/draft.html
#   newsletter-send.sh                            # reads subject from draft.html <title> and body from draft.html
#   newsletter-send.sh "Subject" body.html --meta sidecar.json
#                                                # ledger appends issue row after successful send

set -euo pipefail

source ~/clawd/config/rick.env 2>/dev/null || true

RESEND_API_KEY="${RESEND_API_KEY:?ERROR: RESEND_API_KEY environment variable is not set}"
FROM_EMAIL="rick@meetrick.ai"
SUBSCRIBERS_FILE="${RICK_DATA_ROOT:-$HOME/rick-vault}/newsletter/subscribers.json"
SENDS_LOG="${RICK_DATA_ROOT:-$HOME/rick-vault}/newsletter/sends-log.md"
DRAFT_FILE="${RICK_DATA_ROOT:-$HOME/rick-vault}/newsletter/draft.html"
WORKSPACE_ROOT="${RICK_WORKSPACE_ROOT:-$HOME/.openclaw/workspace}"

# ── Parse args ─────────────────────────────────────────────────────────────────
# Positional: SUBJECT BODY_FILE  (legacy, preserved)
# Optional flags after positionals: --meta <sidecar.json>
SUBJECT=""
BODY_FILE=""
META_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --meta) META_FILE="$2"; shift 2 ;;
    *)
      if [[ -z "$SUBJECT" ]]; then SUBJECT="$1"
      elif [[ -z "$BODY_FILE" ]]; then BODY_FILE="$1"
      else echo "Unknown extra arg: $1" >&2; exit 1
      fi
      shift ;;
  esac
done

# If no subject given, try to extract from draft <title>
if [[ -z "$SUBJECT" ]]; then
  if [[ -f "$DRAFT_FILE" ]]; then
    SUBJECT=$(grep -oP '(?<=<title>)[^<]+' "$DRAFT_FILE" 2>/dev/null || echo "")
    if [[ -z "$SUBJECT" ]]; then
      echo "ERROR: No subject given and no <title> found in $DRAFT_FILE" >&2
      exit 1
    fi
    echo "Using subject from draft.html: $SUBJECT"
  else
    echo "ERROR: No subject provided and no draft.html found at $DRAFT_FILE" >&2
    echo "Usage: $0 \"Subject line\" [body.html]" >&2
    exit 1
  fi
fi

# Resolve body HTML file
if [[ -z "$BODY_FILE" ]]; then
  BODY_FILE="$DRAFT_FILE"
fi

if [[ ! -f "$BODY_FILE" ]]; then
  echo "ERROR: Body file not found: $BODY_FILE" >&2
  exit 1
fi

HTML_BODY=$(cat "$BODY_FILE")

# ── Load subscribers ───────────────────────────────────────────────────────────
if command -v jq &>/dev/null; then
  SUBSCRIBERS=$(jq -r '.[] | select(.status=="active") | .email' "$SUBSCRIBERS_FILE" 2>/dev/null || echo "")
else
  SUBSCRIBERS=$(grep -o '"email":"[^"]*"' "$SUBSCRIBERS_FILE" 2>/dev/null | sed 's/"email":"//;s/"//' || echo "")
fi

COUNT=$(echo "$SUBSCRIBERS" | grep -c '@' || true)

if [[ $COUNT -eq 0 ]]; then
  echo "No active subscribers found. Nothing to send."
  exit 0
fi

echo "Sending to $COUNT subscribers..."

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SUCCESS=0
FAIL=0
ERRORS=""

# ── Send to each subscriber ────────────────────────────────────────────────────
while IFS= read -r EMAIL; do
  [[ -z "$EMAIL" ]] && continue

  # Use resend-safe-send.sh — enforces one recipient per call (privacy guard)
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if bash "$SCRIPT_DIR/resend-safe-send.sh" \
      --to "$EMAIL" \
      --subject "$SUBJECT" \
      --html-file "$BODY_FILE"; then
    (( SUCCESS++ )) || true
  else
    echo "  ✗ Failed for $EMAIL" >&2
    ERRORS="${ERRORS}\n  - ${EMAIL}: send failed"
    (( FAIL++ )) || true
  fi

  # Rate limit: ~2 req/sec to be safe
  sleep 0.5

done <<< "$SUBSCRIBERS"

# ── Log the send ───────────────────────────────────────────────────────────────
LOG_ENTRY="## ${TIMESTAMP} — Newsletter Send\n- Subject: ${SUBJECT}\n- Recipients: ${COUNT} total | ${SUCCESS} sent | ${FAIL} failed\n- Body file: ${BODY_FILE}\n"
if [[ -n "$ERRORS" ]]; then
  LOG_ENTRY="${LOG_ENTRY}- Errors:${ERRORS}\n"
fi
printf "\n${LOG_ENTRY}\n" >> "$SENDS_LOG"

echo ""
echo "Done. $SUCCESS sent, $FAIL failed. Logged to $SENDS_LOG"

# ── Append to newsletter ledger (memory check input for next issue) ──────────
# Only appends if --meta sidecar was provided and at least one send succeeded.
# Re-reads the sidecar at send time so any operator edits to the .md/.json
# before send are reflected in the ledger row.
if [[ -n "$META_FILE" && $SUCCESS -gt 0 && -f "$META_FILE" ]]; then
  echo "→ appending issue row to newsletter ledger from $META_FILE"
  if (cd "$WORKSPACE_ROOT" && python3 - "$META_FILE" "$TIMESTAMP" "$SUCCESS" "$COUNT" <<'PY'
import json, sys
from pathlib import Path
from runtime.newsletter_memory import LEDGER_PATH, append_issue
meta_path, ts, success, total = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
row = json.loads(Path(meta_path).read_text(encoding="utf-8"))
row["sent_at"] = ts
row["date"] = ts[:10]
row["sent_to_count"] = success
row["audience_count"] = total
append_issue(LEDGER_PATH, row)
print(f"  ledger row appended: issue #{row.get('issue')} → {LEDGER_PATH}")
PY
  ); then
    echo "  ledger append: OK"
  else
    echo "  ledger append: FAILED (send succeeded; manually run runtime.newsletter_memory.append_issue if needed)" >&2
  fi
fi
