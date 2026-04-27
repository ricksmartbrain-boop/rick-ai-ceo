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

WORKSPACE="${RICK_WORKSPACE_ROOT:-$ROOT_DIR}"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_SRC_DIR="$ROOT_DIR/deploy/launchd"

mkdir -p "$LAUNCH_AGENTS_DIR"
mkdir -p "$DATA_ROOT/logs"

escape_sed() {
  printf '%s\n' "$1" | sed 's/[&/\|]/\\&/g'
}

install_plist() {
  local src="$1"
  local label="$2"
  local dst="$LAUNCH_AGENTS_DIR/${label}.plist"

  if [[ ! -f "$src" ]]; then
    echo "WARN: Template not found: $src" >&2
    return 1
  fi

  # Unload existing if present
  if launchctl list 2>/dev/null | grep -q "$label"; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null \
      || launchctl unload "$dst" 2>/dev/null \
      || true
  fi

  local safe_ws safe_dr
  safe_ws="$(escape_sed "$WORKSPACE")"
  safe_dr="$(escape_sed "$DATA_ROOT")"

  sed \
    -e "s|__RICK_WORKSPACE__|${safe_ws}|g" \
    -e "s|__RICK_DATA_ROOT__|${safe_dr}|g" \
    "$src" > "$dst"

  # Verify no unreplaced placeholders remain
  if grep -q '__RICK_' "$dst"; then
    echo "ERROR: Unreplaced placeholders in $dst" >&2
    rm -f "$dst"
    return 1
  fi

  chmod 644 "$dst"

  echo "Installed: $dst"
  echo "  Workspace: $WORKSPACE"
  echo "  Data root: $DATA_ROOT"
}

install_plist "$PLIST_SRC_DIR/ai.rick.daemon.plist.example" "ai.rick.daemon"
install_plist "$PLIST_SRC_DIR/ai.rick.daily-proof-engine.plist.example" "ai.rick.daily-proof-engine"
install_plist "$PLIST_SRC_DIR/ai.rick.demo-video-weekly.plist.example" "ai.rick.demo-video-weekly"
install_plist "$PLIST_SRC_DIR/ai.rick.bounce-rate-guardian.plist.example" "ai.rick.bounce-rate-guardian"

cat <<'EOF'

Plist installed. To activate:

  launchctl load ~/Library/LaunchAgents/ai.rick.daemon.plist
  launchctl load ~/Library/LaunchAgents/ai.rick.daily-proof-engine.plist
  launchctl load ~/Library/LaunchAgents/ai.rick.demo-video-weekly.plist
  launchctl load ~/Library/LaunchAgents/ai.rick.bounce-rate-guardian.plist

To check status:

  launchctl list | grep ai.rick

To stop:

  launchctl unload ~/Library/LaunchAgents/ai.rick.daemon.plist
  launchctl unload ~/Library/LaunchAgents/ai.rick.daily-proof-engine.plist
  launchctl unload ~/Library/LaunchAgents/ai.rick.demo-video-weekly.plist
  launchctl unload ~/Library/LaunchAgents/ai.rick.bounce-rate-guardian.plist
EOF
