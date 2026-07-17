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
SUPPRESSION_FILE="${RICK_DATA_ROOT:-$HOME/rick-vault}/mailbox/suppression.txt"
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

# ── Dedup gate: abort if subject or issue already sent ─────────────────────────
# Prevents triple-sends by checking newsletter-ledger.jsonl before touching
# a single subscriber. Exit code 3 = already sent (distinct from auth/other errors).
_dedup_check() {
  local subject="$1"
  local meta_file="$2"
  local ledger="${RICK_DATA_ROOT:-$HOME/rick-vault}/operations/newsletter-ledger.jsonl"
  if [[ ! -f "$ledger" ]]; then return 0; fi

  # Subject-level dedup
  if [[ -n "$subject" ]]; then
    local match
    match=$(python3 - "$ledger" "$subject" <<'PY'
import json, sys
ledger, subject = sys.argv[1], sys.argv[2].strip().lower()
for line in open(ledger):
    line = line.strip()
    if not line: continue
    try:
        row = json.loads(line)
        sent = row.get('sent_at','').strip()
        row_subj = (row.get('subject') or '').strip().lower()
        if sent and row_subj == subject:
            print(f"DUPLICATE: issue={row.get('issue')} sent_at={sent}")
            sys.exit(1)
    except: pass
PY
    )
    if [[ $? -ne 0 ]]; then
      echo "ERROR: DEDUP GATE — Subject '${subject}' already appears in newsletter ledger with sent_at set." >&2
      echo "  $match" >&2
      echo "  This send is blocked to prevent a repeat delivery to subscribers." >&2
      echo "  If this is intentional (different issue), change the subject line." >&2
      exit 3
    fi
  fi

  # Issue-number dedup (only when --meta provided)
  if [[ -n "$meta_file" && -f "$meta_file" ]]; then
    local issue_num
    issue_num=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('issue',''))" "$meta_file" 2>/dev/null || echo '')
    if [[ -n "$issue_num" && "$issue_num" != "0" ]]; then
      local issue_match
      issue_match=$(python3 - "$ledger" "$issue_num" <<'PY'
import json, sys
ledger, num = sys.argv[1], sys.argv[2]
for line in open(ledger):
    line = line.strip()
    if not line: continue
    try:
        row = json.loads(line)
        sent = row.get('sent_at','').strip()
        if sent and str(row.get('issue','')) == num:
            print(f"DUPLICATE: issue={num} sent_at={sent}")
            sys.exit(1)
    except: pass
PY
      )
      if [[ $? -ne 0 ]]; then
        echo "ERROR: DEDUP GATE — Issue #${issue_num} already has sent_at in ledger." >&2
        echo "  $issue_match" >&2
        echo "  This send is blocked. Use a new issue number for a fresh newsletter." >&2
        exit 3
      fi
    fi
  fi
}

_dedup_check "$SUBJECT" "$META_FILE"

# ── Load eligible subscribers ─────────────────────────────────────────────────
# Filter before the send loop so suppressed and reserved test records do not
# appear as delivery failures or reach the provider at all.
RECIPIENTS_FILE=$(mktemp "${TMPDIR:-/tmp}/newsletter-recipients.XXXXXX")
trap 'rm -f "$RECIPIENTS_FILE"' EXIT

FILTER_SUMMARY=$(python3 - "$SUBSCRIBERS_FILE" "$SUPPRESSION_FILE" "$RECIPIENTS_FILE" <<'PY'
import json
import sys
from pathlib import Path

subscribers_path = Path(sys.argv[1])
suppression_path = Path(sys.argv[2])
recipients_path = Path(sys.argv[3])

subscribers = json.loads(subscribers_path.read_text(encoding="utf-8"))
suppressed = set()
if suppression_path.exists():
    for raw_line in suppression_path.read_text(encoding="utf-8", errors="replace").splitlines():
        address = raw_line.split("#", 1)[0].strip().lower()
        if address:
            suppressed.add(address)

active = {
    str(item.get("email", "")).strip().lower()
    for item in subscribers
    if item.get("status") == "active" and str(item.get("email", "")).strip()
}
provenance_missing = {
    str(item.get("email", "")).strip().lower()
    for item in subscribers
    if item.get("status") == "active"
    and str(item.get("email", "")).strip()
    and (not str(item.get("source", "")).strip() or not str(item.get("subscribed_at", "")).strip())
}
reserved = {
    address
    for address in active
    if address.endswith("@example.com") or address.endswith("@test.com")
}
eligible = sorted(active - suppressed - reserved - provenance_missing)
recipients_path.write_text("\n".join(eligible) + ("\n" if eligible else ""), encoding="utf-8")
print(
    f"eligible={len(eligible)} suppressed={len(active & suppressed)} "
    f"reserved={len(reserved)} provenance_missing={len(provenance_missing)}"
)
PY
)

SUBSCRIBERS=$(cat "$RECIPIENTS_FILE")

COUNT=$(echo "$SUBSCRIBERS" | grep -c '@' || true)

if [[ $COUNT -eq 0 ]]; then
  echo "No active subscribers found. Nothing to send."
  exit 0
fi

echo "Sending to $COUNT eligible subscribers ($FILTER_SUMMARY)..."

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
LOG_ENTRY="## ${TIMESTAMP} — Newsletter Send\n- Subject: ${SUBJECT}\n- Recipients: ${COUNT} eligible | ${SUCCESS} sent | ${FAIL} failed\n- Eligibility: ${FILTER_SUMMARY}\n- Body file: ${BODY_FILE}\n"
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
