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

bash "$ROOT_DIR/scripts/bootstrap.sh" >/dev/null
python3 "$ROOT_DIR/runtime/runner.py" work --limit "${1:-3}"
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record --kind system-run --title "Runtime worker" --status done --area runtime --project rick-v6 --route analysis --notes "Processed queued Rick runtime jobs." >/dev/null || true
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" summary --write >/dev/null
python3 "$ROOT_DIR/skills/executive-control/scripts/update-scoreboard.py"

