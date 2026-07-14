#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

WORKSPACE_DIR="${RICK_OPENCLAW_HOME:-$ROOT_DIR}"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
TZ_NAME="${RICK_CRON_TZ:-America/New_York}"
LOG_DIR="${RICK_CRON_LOG_DIR:-$DATA_ROOT/logs/cron}"
FORCE_INBOX="${FORCE_INBOX_CRON:-0}"

mkdir -p "$LOG_DIR"

if ! command -v crontab >/dev/null 2>&1; then
  echo "ERROR: crontab is required but not found" >&2
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT
(crontab -l 2>/dev/null || true) > "$TMP_FILE"

if grep -q '^# RICK_CRON_BEGIN$' "$TMP_FILE"; then
  awk '
    BEGIN {skip=0}
    /^# RICK_CRON_BEGIN$/ {skip=1; next}
    /^# RICK_CRON_END$/ {skip=0; next}
    skip==0 {print}
  ' "$TMP_FILE" > "${TMP_FILE}.clean"
  mv "${TMP_FILE}.clean" "$TMP_FILE"
fi

heartbeat_cmd="[[ -f \"$WORKSPACE_DIR/config/rick.env\" ]] && source \"$WORKSPACE_DIR/config/rick.env\"; bash \"$WORKSPACE_DIR/scripts/run-heartbeat.sh\""
nightly_cmd="[[ -f \"$WORKSPACE_DIR/config/rick.env\" ]] && source \"$WORKSPACE_DIR/config/rick.env\"; bash \"$WORKSPACE_DIR/scripts/run-nightly.sh\""
weekly_cmd="[[ -f \"$WORKSPACE_DIR/config/rick.env\" ]] && source \"$WORKSPACE_DIR/config/rick.env\"; bash \"$WORKSPACE_DIR/scripts/run-weekly.sh\""
logdigest_cmd="[[ -f \"$WORKSPACE_DIR/config/rick.env\" ]] && source \"$WORKSPACE_DIR/config/rick.env\"; bash \"$WORKSPACE_DIR/scripts/run-log-digest.sh\""
inbox_cmd="[[ -f \"$WORKSPACE_DIR/config/rick.env\" ]] && source \"$WORKSPACE_DIR/config/rick.env\"; bash \"$WORKSPACE_DIR/skills/email-automation/scripts/email-triage.sh\" --summary"

{
  echo "# RICK_CRON_BEGIN"
  echo "CRON_TZ=$TZ_NAME"
  echo "*/30 * * * * /bin/zsh -lc '$heartbeat_cmd' >> \"$LOG_DIR/heartbeat.log\" 2>&1"
  echo "0 3 * * * /bin/zsh -lc '$nightly_cmd' >> \"$LOG_DIR/nightly.log\" 2>&1"
  echo "0 2 * * 0 /bin/zsh -lc '$weekly_cmd' >> \"$LOG_DIR/weekly.log\" 2>&1"
  echo "0 9 * * * /bin/zsh -lc '$logdigest_cmd' >> \"$LOG_DIR/log-digest.log\" 2>&1"
  if [[ "$FORCE_INBOX" == "1" || -s "$HOME/.config/himalaya/config.toml" ]]; then
    echo "0 * * * * /bin/zsh -lc '$inbox_cmd' >> \"$LOG_DIR/inbox.log\" 2>&1"
  else
    echo "# inbox cron skipped (configure ~/.config/himalaya/config.toml or set FORCE_INBOX_CRON=1)"
  fi
  echo "# RICK_CRON_END"
} >> "$TMP_FILE"

crontab "$TMP_FILE"

echo "Installed Rick cron jobs with timezone: $TZ_NAME"
crontab -l | sed -n '/RICK_CRON_BEGIN/,/RICK_CRON_END/p'
