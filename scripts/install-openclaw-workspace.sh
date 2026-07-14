#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_DIR="${RICK_OPENCLAW_HOME:-$HOME/clawd}"
FORCE=false

usage() {
  cat <<EOF
Usage: install-openclaw-workspace.sh [OPTIONS]

Install Rick v6 into an OpenClaw workspace.

Options:
  --target <dir>   Target OpenClaw workspace (default: \$RICK_OPENCLAW_HOME or ~/clawd)
  --force          Allow syncing into an existing non-empty workspace
  -h, --help       Show this help
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      TARGET_DIR="$2"
      shift 2
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

TARGET_DIR="${TARGET_DIR/#\~/$HOME}"

if [[ "$TARGET_DIR" == "$ROOT_DIR" ]]; then
  echo "Target already points at the current Rick workspace: $ROOT_DIR"
  exit 0
fi

if [[ -d "$TARGET_DIR" ]] && [[ -n "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]] && [[ "$FORCE" != "true" ]]; then
  echo "Refusing to sync into non-empty workspace without --force: $TARGET_DIR" >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude 'config/rick.env' \
    "$ROOT_DIR/" "$TARGET_DIR/"
else
  cp -R "$ROOT_DIR/." "$TARGET_DIR/"
  rm -f "$TARGET_DIR/config/rick.env"
fi

echo "Installed Rick v6 workspace into: $TARGET_DIR"
echo "Next:"
echo "  1. Read: $TARGET_DIR/START_HERE.md"
echo "  2. Review: $TARGET_DIR/OPENCLAW_PROFILE.md"
echo "  3. Run: bash $TARGET_DIR/scripts/setup.sh --yes"
echo "  4. Review: $TARGET_DIR/config/rick.env"
echo "  5. Run: bash $TARGET_DIR/scripts/preflight-openclaw.sh"
echo "  6. Run: bash $TARGET_DIR/scripts/bootstrap.sh"
echo "  7. Run: bash $TARGET_DIR/scripts/doctor.sh"
echo "  8. Start: bash $TARGET_DIR/scripts/run-daemon.sh"
