#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
NOW="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

mkdir -p "$DATA_ROOT/control" "$DATA_ROOT/operations"

# --- Collect metrics ---
disk_pct="$(df -h / | awk 'NR==2{print $5}')"
disk_avail="$(df -h / | awk 'NR==2{print $4}')"

mem_pressure="$(memory_pressure 2>/dev/null | head -1 || echo 'unknown')"

load_avg="$(uptime | sed 's/.*load averages*: *//')"

check_proc() {
  if pgrep -f "$1" >/dev/null 2>&1; then
    echo "running"
  else
    echo "stopped"
  fi
}

rick_daemon="$(check_proc 'run-daemon.sh' || true)"
openclaw="$(check_proc 'openclaw' || true)"

# --- Write markdown (atomic) ---
tmp="$(mktemp)"
cat <<EOF > "$tmp"
---
updated: $NOW
type: system-health
---

# System Health

| Metric | Value |
|--------|-------|
| Disk used | $disk_pct |
| Disk available | $disk_avail |
| Memory pressure | $mem_pressure |
| Load average | $load_avg |

## Processes

| Process | Status |
|---------|--------|
| rick-daemon | $rick_daemon |
| openclaw | $openclaw |
EOF
mv "$tmp" "$DATA_ROOT/control/system-health.md"

# --- Append JSONL ---
cat <<EOF >> "$DATA_ROOT/operations/system-health.jsonl"
{"ts":"$NOW","disk_pct":"$disk_pct","disk_avail":"$disk_avail","mem_pressure":"$mem_pressure","load":"$load_avg","rick_daemon":"$rick_daemon","openclaw":"$openclaw"}
EOF

echo "system-health: wrote control/system-health.md"
