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
bash "$ROOT_DIR/scripts/doctor.sh" --quiet
bash "$ROOT_DIR/scripts/guardrails-audit.sh" >/dev/null || true
bash "$ROOT_DIR/scripts/health-check.sh" >/dev/null || true
bash "$ROOT_DIR/skills/self-healing-ops/scripts/watchdog.sh" || true
python3 "$ROOT_DIR/skills/obsidian-memory/scripts/rebuild-memory-index.py" rebuild --write --quiet >/dev/null || true
python3 "$ROOT_DIR/runtime/runner.py" heartbeat --work-limit 6 >/dev/null
python3 "$ROOT_DIR/skills/email-automation/scripts/email-sequence-dispatch.py" >/dev/null || true
python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/rick-exec.py" weekly --write
python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/rick-exec.py" score --write
python3 "$ROOT_DIR/skills/reflection-engine/scripts/weekly-retro.py"
python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/initiative-scanner.py" || true
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record --kind system-run --title "Weekly loop" --status done --area executive-control --project rick-v6 --route strategy --notes "Doctor, guardrails audit, health-target check, watchdog/service check, memory index rebuild, runtime heartbeat/work, email sequence dispatch, weekly review, score refresh, weekly retro, scoreboard refresh." >/dev/null
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" summary --write >/dev/null
python3 "$ROOT_DIR/skills/token-economics/scripts/token-usage.py" report --write >/dev/null
python3 "$ROOT_DIR/skills/executive-control/scripts/update-scoreboard.py"

echo "Weekly synthesis complete."
