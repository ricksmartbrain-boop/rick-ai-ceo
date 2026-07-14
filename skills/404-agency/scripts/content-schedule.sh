#!/usr/bin/env bash
# Review 404 Agency content pipeline and suggest posting schedule.
#
# Usage:
#   content-schedule.sh         # Show pending content
#   content-schedule.sh --week  # Weekly content plan

set -euo pipefail

VAULT_AGENCY="$HOME/telegram-sync/vault-output/404 Model Agency"
SHOW_WEEK=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --week) SHOW_WEEK=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "======================================="
echo "  404 Agency -- Content Pipeline"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "======================================="
echo ""

# Check Content Log
CONTENT_LOG="$VAULT_AGENCY/Content Log.md"
if [ -f "$CONTENT_LOG" ]; then
    echo "Content Ideas (from Content Log.md):"
    echo "---"
    grep -A 2 "^- " "$CONTENT_LOG" 2>/dev/null | head -30 || echo "  No items found"
    echo ""
else
    echo "Warning: Content Log not found at $CONTENT_LOG"
fi

# Check Tasks
TASKS="$VAULT_AGENCY/Tasks.md"
if [ -f "$TASKS" ]; then
    echo "Active Tasks:"
    echo "---"
    # Show unchecked tasks
    grep "^\- \[ \]" "$TASKS" 2>/dev/null | head -15 || echo "  No pending tasks"
    echo ""
    echo "Completed Tasks:"
    grep "^\- \[x\]" "$TASKS" 2>/dev/null | tail -5 || echo "  None"
    echo ""
else
    echo "Warning: Tasks file not found at $TASKS"
fi

# Check recent sync logs
echo "Recent Activity (last 3 days):"
echo "---"
for i in 0 1 2; do
    day=$(date -v-"${i}d" "+%Y-%m-%d" 2>/dev/null || date -d "$i days ago" "+%Y-%m-%d" 2>/dev/null || echo "")
    if [ -n "$day" ] && [ -f "$VAULT_AGENCY/Sync Log/$day.md" ]; then
        count=$(wc -l < "$VAULT_AGENCY/Sync Log/$day.md" | tr -d ' ')
        echo "  $day -- $count lines"
    fi
done
echo ""

if $SHOW_WEEK; then
    echo "Suggested Weekly Plan:"
    echo "---"
    echo "  Mon: Cat Instagram post (lifestyle/aesthetic)"
    echo "  Tue: Luna Instagram post (lifestyle/aesthetic)"
    echo "  Wed: Cat Instagram story (behind the scenes)"
    echo "  Thu: Luna Instagram story (behind the scenes)"
    echo "  Fri: Cat Instagram reel (trending audio)"
    echo "  Sat: Luna Instagram reel (trending audio)"
    echo "  Sun: Review metrics, plan next week"
    echo ""
    echo "Aim for 1 post + 2 stories per model per week minimum."
fi
