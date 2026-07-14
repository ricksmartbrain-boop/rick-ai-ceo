#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

TARGET_WORKSPACE="${RICK_OPENCLAW_HOME:-$HOME/clawd}"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
REPORT_FILE="${DATA_ROOT}/control/openclaw-preflight.md"
TELEGRAM_BOT_TOKEN="${RICK_TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${RICK_TELEGRAM_ALLOWED_CHAT_ID:-}"
TELEGRAM_THREAD_MODE="${RICK_TELEGRAM_THREAD_MODE:-off}"
TELEGRAM_FORUM_CHAT_ID="${RICK_TELEGRAM_FORUM_CHAT_ID:-}"
XPOST_BIN="${RICK_XPOST_BIN:-$TARGET_WORKSPACE/bin/xpost}"
OPENCLAW_MAIN_AGENT_ID="${RICK_OPENCLAW_MAIN_AGENT_ID:-rick}"
SESSION_POLICY_FILE="${RICK_OPENCLAW_SESSION_POLICY_FILE:-$TARGET_WORKSPACE/config/openclaw-session-policy.json}"
AGENT_BLUEPRINT_FILE="${RICK_OPENCLAW_AGENT_BLUEPRINT_FILE:-$TARGET_WORKSPACE/config/openclaw-agent-blueprint.json}"
MEMORY_FLUSH_PROMPT_FILE="${RICK_OPENCLAW_MEMORY_FLUSH_PROMPT_FILE:-$TARGET_WORKSPACE/templates/openclaw/memory-flush.prompt.md}"
SECURE_DM_MODE="${RICK_OPENCLAW_SECURE_DM_MODE:-prepared}"

critical_bins=(brew node npm python3 tmux jq openclaw)
recommended_bins=(gh himalaya codex claude ralphy xpost vercel stripe)
missing_critical=()
missing_recommended=()

for bin in "${critical_bins[@]}"; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    missing_critical+=("$bin")
  fi
done

for bin in "${recommended_bins[@]}"; do
  if [[ "$bin" == "xpost" && -x "${XPOST_BIN/#\~/$HOME}" ]]; then
    continue
  fi
  if ! command -v "$bin" >/dev/null 2>&1; then
    missing_recommended+=("$bin")
  fi
done

node_version="$(node --version 2>/dev/null || true)"
node_major=""
if [[ -n "$node_version" ]]; then
  node_major="$(echo "$node_version" | sed -E 's/^v([0-9]+).*/\1/')"
fi

session_policy_ok=false
agent_blueprint_ok=false
memory_flush_prompt_ok=false
session_policy_expanded="${SESSION_POLICY_FILE/#\~/$HOME}"
agent_blueprint_expanded="${AGENT_BLUEPRINT_FILE/#\~/$HOME}"
memory_flush_prompt_expanded="${MEMORY_FLUSH_PROMPT_FILE/#\~/$HOME}"

if [[ -f "$session_policy_expanded" ]] && jq empty "$session_policy_expanded" >/dev/null 2>&1; then
  session_policy_ok=true
fi
if [[ -f "$agent_blueprint_expanded" ]] && jq empty "$agent_blueprint_expanded" >/dev/null 2>&1; then
  agent_blueprint_ok=true
fi
if [[ -f "$memory_flush_prompt_expanded" ]]; then
  memory_flush_prompt_ok=true
fi

status="ready"
if [[ ${#missing_critical[@]} -gt 0 ]]; then
  status="blocked"
fi
if [[ -n "$node_major" && "$node_major" -lt 22 ]]; then
  status="blocked"
fi
if [[ "$session_policy_ok" != "true" || "$agent_blueprint_ok" != "true" || "$memory_flush_prompt_ok" != "true" ]]; then
  status="blocked"
fi
if [[ "$TELEGRAM_THREAD_MODE" != "off" && ( -z "$TELEGRAM_FORUM_CHAT_ID" || "$session_policy_ok" != "true" ) ]]; then
  status="blocked"
fi
if [[ "$SECURE_DM_MODE" == "enabled" && "$session_policy_ok" != "true" ]]; then
  status="blocked"
fi

mkdir -p "$(dirname "$REPORT_FILE")"

{
  echo "# OpenClaw Preflight"
  echo
  echo "- Generated: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "- Target workspace: $TARGET_WORKSPACE"
  echo "- Rick vault: $DATA_ROOT"
  echo "- Status: $status"
  echo
  echo "## Critical CLIs"
  echo
  for bin in "${critical_bins[@]}"; do
    if command -v "$bin" >/dev/null 2>&1; then
      echo "- $bin: ok ($(command -v "$bin"))"
    else
      echo "- $bin: missing"
    fi
  done
  echo
  echo "## Recommended CLIs"
  echo
  for bin in "${recommended_bins[@]}"; do
    if [[ "$bin" == "xpost" && -x "${XPOST_BIN/#\~/$HOME}" ]]; then
      echo "- $bin: ok (${XPOST_BIN/#\~/$HOME})"
    elif command -v "$bin" >/dev/null 2>&1; then
      echo "- $bin: ok ($(command -v "$bin"))"
    else
      echo "- $bin: missing"
    fi
  done
  echo
  echo "## Node"
  echo
  if [[ -n "$node_version" ]]; then
    echo "- version: $node_version"
  else
    echo "- version: missing"
  fi
  if [[ -n "$node_major" && "$node_major" -lt 22 ]]; then
    echo "- requirement: blocked (need Node 22+)"
  else
    echo "- requirement: ok"
  fi
  echo
  echo "## Workspace"
  echo
  if [[ -d "$TARGET_WORKSPACE" ]]; then
    echo "- OpenClaw workspace exists"
  else
    echo "- OpenClaw workspace missing"
  fi
  if [[ -d "$DATA_ROOT" ]]; then
    echo "- Rick vault exists"
  else
    echo "- Rick vault missing (bootstrap will create it)"
  fi
  echo
  echo "## OpenClaw Profile"
  echo
  echo "- Main agent id: $OPENCLAW_MAIN_AGENT_ID"
  echo "- Telegram thread mode: $TELEGRAM_THREAD_MODE"
  if [[ -n "$TELEGRAM_FORUM_CHAT_ID" ]]; then
    echo "- Telegram forum chat id: configured"
  else
    echo "- Telegram forum chat id: missing"
  fi
  if [[ "$session_policy_ok" == "true" ]]; then
    echo "- Session policy: ok ($session_policy_expanded)"
  else
    echo "- Session policy: missing or invalid ($session_policy_expanded)"
  fi
  if [[ "$agent_blueprint_ok" == "true" ]]; then
    echo "- Agent blueprint: ok ($agent_blueprint_expanded)"
  else
    echo "- Agent blueprint: missing or invalid ($agent_blueprint_expanded)"
  fi
  if [[ "$memory_flush_prompt_ok" == "true" ]]; then
    echo "- Memory flush prompt: ok ($memory_flush_prompt_expanded)"
  else
    echo "- Memory flush prompt: missing ($memory_flush_prompt_expanded)"
  fi
  echo "- Secure DM mode: $SECURE_DM_MODE"
  if [[ "$SECURE_DM_MODE" == "enabled" ]]; then
    echo "- Secure DM note: only enable after launch stability is proven and isolated DM scoping is configured"
  else
    echo "- Secure DM note: prepared but off is the expected pre-launch posture"
  fi
  echo
  echo "## Suggested Install Commands"
  echo
  echo "- brew install node@22 python@3.12 tmux jq gh himalaya"
  echo "- npm install -g pnpm openclaw@latest ralphy-cli @openai/codex @anthropic-ai/claude-code vercel"
  echo "- brew install stripe/stripe-cli/stripe"
  echo "- openclaw onboard --install-daemon"
  echo
  echo "## Founder Control"
  echo
  if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
    echo "- Telegram bot token: configured"
  else
    echo "- Telegram bot token: missing"
  fi
  if [[ -n "$TELEGRAM_CHAT_ID" ]]; then
    echo "- Telegram allowed chat id: configured"
  else
    echo "- Telegram allowed chat id: missing"
  fi
  echo "- Session keys appear in: python3 $TARGET_WORKSPACE/runtime/runner.py status"
  echo
} > "$REPORT_FILE"

cat "$REPORT_FILE"

if [[ ${#missing_critical[@]} -gt 0 ]]; then
  exit 1
fi

if [[ -n "$node_major" && "$node_major" -lt 22 ]]; then
  exit 1
fi

if [[ "$status" == "blocked" ]]; then
  exit 1
fi
