#!/usr/bin/env bash
# Full 404 Model Agency dashboard.
#
# Shows: bot status, model metrics, tasks, content pipeline, revenue.

set -euo pipefail

VAULT_AGENCY="$HOME/telegram-sync/vault-output/404 Model Agency"
BOT_DIR="$HOME/telegram-sync"

echo "======================================="
echo "  404 Model Agency -- Status Dashboard"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "======================================="
echo ""

# 1. Bot Status
echo "Bot Status"
echo "---"
if pgrep -f "telegram-sync/bot.py" > /dev/null 2>&1; then
    echo "  Bot is RUNNING"
    pid=$(pgrep -f "telegram-sync/bot.py" | head -1)
    echo "  PID: $pid"
else
    echo "  Bot is NOT RUNNING"
    echo "  Restart: launchctl load ~/Library/LaunchAgents/com.vlad.telegram-sync.plist"
fi

# Last log entry
if [ -f "$BOT_DIR/logs/bot.log" ]; then
    last_log=$(tail -1 "$BOT_DIR/logs/bot.log" 2>/dev/null || echo "No logs")
    echo "  Last log: $last_log"
fi
echo ""

# 2. Model Profiles
echo "Models"
echo "---"
for model_file in "$VAULT_AGENCY/Models/"*.md; do
    if [ -f "$model_file" ]; then
        name=$(basename "$model_file" .md)
        echo "  $name"
        # Try to extract latest metrics from Performance Log
        grep -A1 "Performance Log" "$model_file" 2>/dev/null | tail -1 | sed 's/^/     /' || true
    fi
done
echo ""

# 3. Task Summary
echo "Tasks"
echo "---"
if [ -f "$VAULT_AGENCY/Tasks.md" ]; then
    pending=$(grep -c "^\- \[ \]" "$VAULT_AGENCY/Tasks.md" 2>/dev/null || echo "0")
    done=$(grep -c "^\- \[x\]" "$VAULT_AGENCY/Tasks.md" 2>/dev/null || echo "0")
    echo "  Pending: $pending"
    echo "  Completed: $done"
else
    echo "  Warning: Tasks file not found"
fi
echo ""

# 4. Recent Sync Activity
echo "Sync Activity (last 7 days)"
echo "---"
total_messages=0
for i in $(seq 0 6); do
    day=$(date -v-"${i}d" "+%Y-%m-%d" 2>/dev/null || date -d "$i days ago" "+%Y-%m-%d" 2>/dev/null || echo "")
    if [ -n "$day" ] && [ -f "$VAULT_AGENCY/Sync Log/$day.md" ]; then
        count=$(wc -l < "$VAULT_AGENCY/Sync Log/$day.md" | tr -d ' ')
        total_messages=$((total_messages + count))
        echo "  $day: $count lines"
    fi
done
echo "  Total: $total_messages lines"
echo ""

# 5. Revenue Status
echo "Revenue"
echo "---"
echo "  Target: \$5,000/month"
echo "  Current: \$0 (pre-revenue phase)"
echo "  Strategy: Grow to 5K followers then Fanvue + brand deals"
echo ""

echo "======================================="
echo "  Use 'agency-metrics.sh' for live social metrics"
echo "======================================="
