#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

echo "=== Rick Autonomy Activation ==="
echo

# --- Preflight ---
echo "[1/6] Preflight checks..."

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FATAL: $ENV_FILE not found. Run bootstrap first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

missing=()
[[ -z "${RICK_DATA_ROOT:-}" ]] && missing+=("RICK_DATA_ROOT")
[[ -z "${RICK_TELEGRAM_BOT_TOKEN:-}" ]] && missing+=("RICK_TELEGRAM_BOT_TOKEN")

has_llm=false
[[ -n "${OPENAI_API_KEY:-}" ]] && has_llm=true
[[ -n "${ANTHROPIC_API_KEY:-}" ]] && has_llm=true
[[ -n "${GOOGLE_API_KEY:-}${GEMINI_API_KEY:-}" ]] && has_llm=true
[[ -n "${RICK_LLM_GATEWAY_URL:-}" ]] && has_llm=true
$has_llm || missing+=("at least one LLM key (OPENAI/ANTHROPIC/GOOGLE/GEMINI or RICK_LLM_GATEWAY_URL)")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "FATAL: Missing critical env vars:" >&2
  for m in "${missing[@]}"; do echo "  - $m" >&2; done
  exit 1
fi
echo "  Preflight OK."

# --- Init runtime DB ---
echo "[2/6] Initializing runtime DB..."
python3 "$ROOT_DIR/runtime/runner.py" init
echo "  DB ready."

# --- Install crons ---
echo "[3/6] Installing cron jobs..."
bash "$ROOT_DIR/scripts/install-crons.sh"

# --- Install + load launchd ---
echo "[4/6] Installing launchd plists..."
bash "$ROOT_DIR/scripts/install-launchd.sh"

echo "[5/6] Loading launchd agents..."
LAUNCH_DIR="$HOME/Library/LaunchAgents"
for plist in ai.rick.daemon ai.rick.daily-proof-engine ai.rick.demo-video-weekly; do
  plist_path="$LAUNCH_DIR/${plist}.plist"
  if [[ -f "$plist_path" ]]; then
    launchctl load "$plist_path" 2>/dev/null || true
    echo "  Loaded $plist"
  else
    echo "  WARN: $plist_path not found, skipping" >&2
  fi
done

# --- Verify ---
echo "[6/6] Verifying..."
errors=()

if ! crontab -l 2>/dev/null | grep -q 'RICK_CRON_BEGIN'; then
  errors+=("Cron block not found")
fi
if ! launchctl list 2>/dev/null | grep -q 'ai.rick.daemon'; then
  errors+=("ai.rick.daemon not loaded")
fi
if ! launchctl list 2>/dev/null | grep -q 'ai.rick.demo-video-weekly'; then
  errors+=("ai.rick.demo-video-weekly not loaded")
fi

if [[ ${#errors[@]} -gt 0 ]]; then
  echo "WARNINGS:" >&2
  for e in "${errors[@]}"; do echo "  - $e" >&2; done
else
  echo "  All checks passed."
fi

# --- Write confirmation to daily note ---
TODAY="$(date '+%Y-%m-%d')"
DAILY_DIR="$RICK_DATA_ROOT/daily-notes"
mkdir -p "$DAILY_DIR"
DAILY_FILE="$DAILY_DIR/${TODAY}.md"

{
  echo ""
  echo "## Autonomy Activated"
  echo ""
  echo "- Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "- Crons: installed"
  echo "- Daemon: loaded"
  echo "- Demo video weekly: loaded"
  echo "- Telegram bridge: loaded"
  if [[ ${#errors[@]} -gt 0 ]]; then
    echo "- Warnings: ${errors[*]}"
  else
    echo "- Status: all green"
  fi
} >> "$DAILY_FILE"

echo
echo "Autonomy activated. Rick is running on his own."
