#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
OPENCLAW_HOME="${HOME}/.openclaw"
# Gateway log moved on 2026-05-28 (openclaw upgrade): plist StandardOutPath
# now points at ~/Library/Logs/openclaw/. The old ~/.openclaw/logs/ path is
# frozen at May 28 and made this report show stale errors/log lines.
GATEWAY_LOG="$HOME/Library/Logs/openclaw/gateway.log"

mkdir -p "$DATA_ROOT/control"

# --- Gateway process ---
if pgrep -f 'openclaw.*gateway' >/dev/null 2>&1; then
  gw_status="running"
elif pgrep -f 'openclaw' >/dev/null 2>&1; then
  gw_status="running (generic match)"
else
  gw_status="stopped"
fi

# --- Recent log errors/warnings ---
recent_errors=""
recent_warnings=""
if [[ -f "$GATEWAY_LOG" ]]; then
  recent_errors="$(tail -500 "$GATEWAY_LOG" | grep -ci 'error' || true)"
  recent_warnings="$(tail -500 "$GATEWAY_LOG" | grep -ci 'warn' || true)"
  last_lines="$(tail -5 "$GATEWAY_LOG" 2>/dev/null || true)"
else
  recent_errors="n/a (no log file)"
  recent_warnings="n/a"
  last_lines="No gateway log found at $GATEWAY_LOG"
fi

# --- Active agents ---
agents_dir="$OPENCLAW_HOME/agents"
if [[ -d "$agents_dir" ]]; then
  agent_count="$(find "$agents_dir" -maxdepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')"
  agent_list="$(ls "$agents_dir" 2>/dev/null | head -10 || echo 'none')"
else
  agent_count="0"
  agent_list="(agents dir not found)"
fi

# --- Recent Telegram sends ---
tg_log="$DATA_ROOT/logs/telegram-sends.log"
if [[ -f "$tg_log" ]]; then
  tg_recent="$(tail -100 "$tg_log" | wc -l | tr -d ' ')"
else
  tg_recent="n/a (no log)"
fi

# --- Write markdown (atomic) ---
tmp="$(mktemp)"
cat <<EOF > "$tmp"
---
updated: $NOW
type: openclaw-status
---

# OpenClaw Status

| Check | Result |
|-------|--------|
| Gateway process | $gw_status |
| Recent errors (last 500 lines) | $recent_errors |
| Recent warnings (last 500 lines) | $recent_warnings |
| Active agents | $agent_count |
| Recent Telegram sends | $tg_recent |

## Active Agents
\`\`\`
$agent_list
\`\`\`

## Last Gateway Log Lines
\`\`\`
$last_lines
\`\`\`
EOF
mv "$tmp" "$DATA_ROOT/control/openclaw-status.md"

echo "openclaw-health: wrote control/openclaw-status.md"
