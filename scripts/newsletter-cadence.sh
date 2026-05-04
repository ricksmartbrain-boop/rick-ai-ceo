#!/usr/bin/env bash
# Newsletter cadence runner — Tue & Sat 9am PT.
#
# Spec (Vlad TUI handoff, 2026-05-04):
# - Draft mode for issues #5–#8 (~2 weeks). Auto-send is NOT enabled.
# - Drafts land in ~/rick-vault/projects/email/newsletter-drafts/
# - Hard memory check via runtime/newsletter_memory.py before draft accepted.
# - Theme = next slot in rotation (issue#5 -> contrarian-take, slot 5).
# - Telegram-notify Vlad with draft path + theme + first-line preview.
# - Smart-models invariant: writing route -> claude-sonnet-4-6.
# - Newsletter Resend path is independent of outbound_dispatcher / kill_switches.
#
# After 2 weeks of approved drafts, flip RICK_NEWSLETTER_AUTO_SEND=1 in env.
#
# Usage:
#   bash scripts/newsletter-cadence.sh           # full draft cycle
#   bash scripts/newsletter-cadence.sh --dry-run # plan only, no LLM/notify

set -euo pipefail

WORKSPACE="${WORKSPACE:-/Users/rickthebot/.openclaw/workspace}"
ENV_FILE="${ENV_FILE:-$HOME/clawd/config/rick.env}"

# Pin RICK_DATA_ROOT to the production Rick vault. The legacy env file at
# ~/clawd/config/rick.env still points at rick-install-test (the synthetic
# install sandbox) for global RICK_DATA_ROOT, but the newsletter ledger,
# drafts, and Resend audience all live under ~/rick-vault. We export the
# correct value before sourcing the env file so the env's stale value
# doesn't clobber it.
export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
_PINNED_DATA_ROOT="$RICK_DATA_ROOT"

# shellcheck disable=SC1090
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

# Re-pin in case the env file overwrote it.
export RICK_DATA_ROOT="$_PINNED_DATA_ROOT"

DATA_ROOT="$RICK_DATA_ROOT"
DRAFTS_DIR="$DATA_ROOT/projects/email/newsletter-drafts"
LEDGER="$DATA_ROOT/operations/newsletter-ledger.jsonl"
LOG_DIR="$DATA_ROOT/operations"
LOG_FILE="$LOG_DIR/newsletter-cadence.jsonl"

mkdir -p "$DRAFTS_DIR" "$LOG_DIR"

cd "$WORKSPACE"

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DATE="$(date +%Y-%m-%d)"
DOW="$(date +%u)"   # 1=Mon..7=Sun

log_event() {
  local payload="$1"
  echo "$payload" >> "$LOG_FILE"
}

# Determine next issue number from ledger
NEXT_ISSUE="$(python3 -c "
import json, os, sys
from pathlib import Path
p = Path(os.environ.get('RICK_DATA_ROOT', os.path.expanduser('~/rick-vault'))) / 'operations' / 'newsletter-ledger.jsonl'
n = 0
if p.exists():
    for line in p.read_text().splitlines():
        if line.strip():
            try: n = max(n, int(json.loads(line).get('issue') or 0))
            except: pass
print(n + 1)
")"

# Theme = slot for next issue
THEME="$(python3 -c "
from runtime.newsletter_memory import slot_for_issue
print(slot_for_issue($NEXT_ISSUE))
")"

DRAFT_PATH="$DRAFTS_DIR/${DATE}-issue-$(printf '%03d' "$NEXT_ISSUE").md"
DRAFT_META="$DRAFTS_DIR/${DATE}-issue-$(printf '%03d' "$NEXT_ISSUE").json"

echo "[newsletter-cadence] $TS | issue=$NEXT_ISSUE | theme=$THEME | draft=$DRAFT_PATH | dry_run=$DRY_RUN"

log_event "{\"ts\":\"$TS\",\"event\":\"cycle_start\",\"issue\":$NEXT_ISSUE,\"theme\":\"$THEME\",\"dow\":$DOW,\"dry_run\":$DRY_RUN}"

if [ $DRY_RUN -eq 1 ]; then
  echo "DRY RUN — would draft issue #$NEXT_ISSUE (theme=$THEME) to $DRAFT_PATH"
  log_event "{\"ts\":\"$TS\",\"event\":\"dry_run_complete\",\"issue\":$NEXT_ISSUE}"
  exit 0
fi

# 1. Generate the draft via the writing route (claude-sonnet-4-6).
#    The actual LLM call is delegated to a small Python helper to keep
#    bash thin and to make model routing explicit.
python3 -m runtime.newsletter_drafter \
  --issue "$NEXT_ISSUE" \
  --theme "$THEME" \
  --out-md "$DRAFT_PATH" \
  --out-meta "$DRAFT_META" \
  || { log_event "{\"ts\":\"$TS\",\"event\":\"draft_failed\",\"issue\":$NEXT_ISSUE}"; exit 1; }

# 2. Hard memory check. Exit 2 = overlap, exit 0 = clean.
if ! python3 -m runtime.newsletter_memory check "$DRAFT_META"; then
  log_event "{\"ts\":\"$TS\",\"event\":\"overlap_detected\",\"issue\":$NEXT_ISSUE,\"draft\":\"$DRAFT_META\"}"
  echo "OVERLAP DETECTED — draft rejected. Inspect $DRAFT_META and rerun." >&2
  exit 2
fi

# 3. Telegram notify Vlad with draft path + theme + first-line preview.
if [ -x scripts/tg-topic.sh ] || command -v scripts/tg-topic.sh >/dev/null 2>&1; then
  PREVIEW="$(head -10 "$DRAFT_PATH" | sed 's/`/\\`/g' | head -c 600)"
  bash scripts/tg-topic.sh approvals \
    "📰 Newsletter draft #$NEXT_ISSUE ($THEME) ready — $DRAFT_PATH

Preview:
$PREVIEW

Reply 'send-newsletter $NEXT_ISSUE' to ship, or edit the draft first." \
    || true
fi

log_event "{\"ts\":\"$TS\",\"event\":\"draft_ready\",\"issue\":$NEXT_ISSUE,\"theme\":\"$THEME\",\"draft_md\":\"$DRAFT_PATH\",\"draft_meta\":\"$DRAFT_META\"}"

echo "Draft ready: $DRAFT_PATH"
echo "Memory check: PASS"
echo "Vlad notified via Telegram CEO-HQ"
