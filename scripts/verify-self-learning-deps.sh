#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="${RICK_OPENCLAW_HOME:-$HOME/.openclaw/workspace}"
missing=0
check() {
  local path="$1"
  if [[ -e "$path" ]]; then
    echo "OK: $path"
  else
    echo "MISSING: $path"
    missing=1
  fi
}

check "$ROOT_DIR/scripts/self-improvement-loop.py"
check "$ROOT_DIR/scripts/generate-hot-context.sh"
check "$ROOT_DIR/scripts/memory-maintenance.py"
check "$ROOT_DIR/skills/self-learning/scripts/experiment-engine.py"
check "$ROOT_DIR/skills/self-learning/scripts/content-signal-tracker.py"

exit $missing
