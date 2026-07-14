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

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

python3 "$ROOT_DIR/skills/claude-monitor/scripts/log-anomaly-digest.py"

python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record \
  --kind system-run \
  --title "Log anomaly digest" \
  --status done \
  --area monitoring \
  --project rick-v6 \
  --route analysis \
  --notes "Log anomaly scan complete." >/dev/null

echo "Log digest complete."
