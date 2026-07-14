#!/bin/bash
set -euo pipefail

SEED="${1:-$HOME/rick-vault/projects/outreach/state-city-seed-all.json}"
CATEGORIES="${2:-dentist,chiropractor,med spa}"
BATCH_SIZE="${BATCH_SIZE:-10}"
PER_COMBO="${PER_COMBO:-4}"
LIMIT="${LIMIT:-20}"
PAUSE_SECONDS="${PAUSE_SECONDS:-15}"

TOTAL=$(python3 - <<PY
import json
from pathlib import Path
p=Path('$HOME/rick-vault/projects/outreach/state-city-seed-all.json').expanduser()
print(len(json.loads(p.read_text())))
PY
)

for (( OFFSET=0; OFFSET<TOTAL; OFFSET+=BATCH_SIZE )); do
  echo "=== State sweep batch offset=$OFFSET size=$BATCH_SIZE ==="
  python3 /Users/rickthebot/.openclaw/workspace/scripts/state-places-sweep.py \
    --seed "$SEED" \
    --categories "$CATEGORIES" \
    --batch-size "$BATCH_SIZE" \
    --offset "$OFFSET" \
    --per-combo "$PER_COMBO" \
    --limit "$LIMIT" \
    --send
  echo "=== batch complete: offset=$OFFSET ==="
  sleep "$PAUSE_SECONDS"
done
