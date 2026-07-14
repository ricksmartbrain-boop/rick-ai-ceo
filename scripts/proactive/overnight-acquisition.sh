#!/usr/bin/env bash
set -euo pipefail
# overnight-acquisition.sh — Multi-channel acquisition during off-hours
# Runs at 2am and 4am PT to ensure overnight coverage
#
# This script orchestrates ALL channels that should be active overnight:
# 1. X post (if gap > 6h)
# 2. Moltbook post (if gap > 6h)  
# 3. LinkedIn engagement check
# 4. Reddit engagement check
# 5. Threads post (if gap > 6h)
# 6. Run experiment engine to queue/launch experiments
# 7. Run signal tracker to collect metrics
# 8. Update growth metrics

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
ROOT_DIR="${RICK_OPENCLAW_HOME:-$HOME/.openclaw/workspace}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SELF_LEARNING="$ROOT_DIR/skills/self-learning/scripts"
LOG_FILE="$DATA_ROOT/logs/overnight-acquisition.log"

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "========== Overnight Acquisition Started =========="

# 1. Proactive push to all channels
log "--- Running proactive push ---"
bash "$SCRIPT_DIR/proactive-push-all-channels.sh" 2>&1 | tee -a "$LOG_FILE" || log "[WARN] Proactive push had errors"

# 2. Run experiment engine
log "--- Running experiment engine ---"
if [[ -f "$SELF_LEARNING/experiment-engine.py" ]]; then
  cd "$SELF_LEARNING" && python3 experiment-engine.py 2>&1 | tee -a "$LOG_FILE" || log "[WARN] Experiment engine had errors"
else
  log "[WARN] experiment-engine.py missing at $SELF_LEARNING"
fi

# 3. Run signal tracker
log "--- Running signal tracker ---"
if [[ -f "$SELF_LEARNING/content-signal-tracker.py" ]]; then
  cd "$SELF_LEARNING" && python3 content-signal-tracker.py 2>&1 | tee -a "$LOG_FILE" || log "[WARN] Signal tracker had errors"
else
  log "[WARN] content-signal-tracker.py missing at $SELF_LEARNING"
fi

# 4. Update brain state with overnight run
python3 -c "
import json
from datetime import datetime
from pathlib import Path

state_path = Path('$DATA_ROOT/brain/state.json')
if state_path.exists():
    state = json.loads(state_path.read_text())
else:
    state = {}

state['last_overnight_run'] = datetime.now().isoformat()
state.setdefault('daily_counters', {})
state['daily_counters']['overnight_runs'] = state['daily_counters'].get('overnight_runs', 0) + 1

state_path.write_text(json.dumps(state, indent=2))
print('[OK] Brain state updated')
"

log "========== Overnight Acquisition Complete =========="
