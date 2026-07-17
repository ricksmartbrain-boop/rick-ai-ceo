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
INTERVAL_SECONDS="${RICK_DAEMON_INTERVAL_SECONDS:-120}"
WORK_LIMIT="${RICK_DAEMON_WORK_LIMIT:-3}"
LOG_DIR="$RICK_DATA_ROOT/logs"
DAEMON_LOG="$LOG_DIR/daemon.log"
MAX_LOG_BYTES=$((5 * 1024 * 1024))  # 5 MB

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"

log() {
  printf '%s [daemon] %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >> "$DAEMON_LOG"
}

rotate_log() {
  if [[ -f "$DAEMON_LOG" ]]; then
    local size
    size=$(stat -f%z "$DAEMON_LOG" 2>/dev/null || stat -c%s "$DAEMON_LOG" 2>/dev/null || echo 0)
    if (( size > MAX_LOG_BYTES )); then
      [[ -f "$DAEMON_LOG.1" ]] && mv "$DAEMON_LOG.1" "$DAEMON_LOG.2"
      mv "$DAEMON_LOG" "$DAEMON_LOG.1"
      log "Log rotated (previous size: ${size} bytes)"
    fi
  fi
}

trap 'log "Stopping Rick daemon."; exit 0' INT TERM

if ! bash "$ROOT_DIR/scripts/bootstrap.sh" >> "$DAEMON_LOG" 2>&1; then
  log "FATAL: bootstrap failed, exiting"
  exit 1
fi

log "Rick daemon started (interval=${INTERVAL_SECONDS}s, work-limit=${WORK_LIMIT})"

# Disable errexit inside the main loop — transient errors must not kill the daemon
set +e

while true; do
  rotate_log

  # ── Nightly catch-up (2026-07-17) ──────────────────────────────────────────
  # StartCalendarInterval never fires while the Mac is in DarkWake (ai.rick.
  # nightly sat at runs=0 through an idle-sleeping 03:10), so the daemon rides
  # shotgun: past 04:00, if no nightly COMPLETED in >26h and none was ATTEMPTED
  # in >6h (bounds retry storms to ~4/day on persistent failure), run it here.
  NIGHTLY_MARKER="$RICK_DATA_ROOT/control/last-nightly-run"
  NIGHTLY_ATTEMPT="$RICK_DATA_ROOT/control/last-nightly-attempt"
  NIGHTLY_LOCK="$RICK_DATA_ROOT/control/nightly-catchup.lock"
  HOUR_NOW=$(date +%H | sed 's/^0//')
  if (( HOUR_NOW >= 4 )); then
    now_epoch=$(date +%s)
    marker_age=$(( now_epoch - $(stat -f%m "$NIGHTLY_MARKER" 2>/dev/null || echo 0) ))
    attempt_age=$(( now_epoch - $(stat -f%m "$NIGHTLY_ATTEMPT" 2>/dev/null || echo 0) ))
    if (( marker_age > 93600 && attempt_age > 21600 )); then
      if mkdir "$NIGHTLY_LOCK" 2>/dev/null; then
        mkdir -p "$RICK_DATA_ROOT/control" "$RICK_DATA_ROOT/logs/cron"
        touch "$NIGHTLY_ATTEMPT"
        log "Nightly catch-up: last completion ${marker_age}s ago — running now"
        if bash "$ROOT_DIR/scripts/run-nightly.sh" >> "$RICK_DATA_ROOT/logs/cron/nightly.log" 2>&1; then
          log "Nightly catch-up: completed"
        else
          log "Nightly catch-up FAILED (see logs/cron/nightly.log) — retry in 6h"
        fi
        rmdir "$NIGHTLY_LOCK" 2>/dev/null
      fi
    fi
  fi

  # Heartbeat stderr goes to its own file (truncated each loop) so a failure's
  # traceback can be embedded in the blocker notes; it is appended to the
  # daemon log right after so nothing is lost from the existing log stream.
  HEARTBEAT_ERR="$LOG_DIR/heartbeat-stderr.last"
  python3 "$ROOT_DIR/runtime/runner.py" heartbeat --work-limit "$WORK_LIMIT" >> "$DAEMON_LOG" 2> "$HEARTBEAT_ERR"
  heartbeat_rc=$?
  cat "$HEARTBEAT_ERR" >> "$DAEMON_LOG"
  if [[ $heartbeat_rc -ne 0 ]]; then
    # exit > 128 means killed by signal (128+N) — no traceback will exist
    log "Heartbeat failed (exit=$heartbeat_rc), recording blocker"
    # execution-ledger.py json.dumps()-es --notes, so passing the raw tail as
    # an argument is JSON-safe without extra escaping here.
    err_tail="$(tail -n 15 "$HEARTBEAT_ERR" 2>/dev/null || true)"
    python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record --kind blocker --title "Rick daemon loop failed" --status blocked --area runtime --project rick-v6 --route heartbeat --notes "Heartbeat or job processing returned exit=$heartbeat_rc from run-daemon.sh. stderr tail: ${err_tail:-<empty>}" >> "$DAEMON_LOG" 2>&1 || log "WARNING: failed to record blocker in execution ledger"
  fi

  python3 "$ROOT_DIR/skills/email-automation/scripts/email-sequence-dispatch.py" >> "$DAEMON_LOG" 2>&1 || true

  # ── Nurture runner (roast + founder-tax lead follow-up) ───────────────────────
  python3 -m runtime.nurture_runner >> "$DAEMON_LOG" 2>&1 || true

  # ── Content engine slot dispatch (time-based, launchd-safe) ─────────────────
  CE_SCRIPT="$ROOT_DIR/../rick-vault/projects/x-twitter/content-engine.sh"
  CE_STATE="$RICK_DATA_ROOT/logs/content-engine-slots.json"
  if [[ -f "$CE_SCRIPT" ]]; then
    HOUR=$(date +%H | sed 's/^0//')
    python3 - "$CE_STATE" "$HOUR" "$CE_SCRIPT" << 'PYEOF2'
import sys, json, subprocess, datetime, pathlib
state_file, hour_str, script = sys.argv[1], sys.argv[2], sys.argv[3]
hour = int(hour_str)
now = datetime.datetime.now()
today = now.strftime("%Y-%m-%d")
state = {}
try: state = json.loads(pathlib.Path(state_file).read_text())
except: pass
slots = {"morning": (7,10), "midday": (11,14), "evening": (17,20), "scout": (9,11)}
for slot, (start, end) in slots.items():
    last_key = f"{today}_{slot}"
    if state.get(last_key): continue
    if start <= hour < end:
        result = subprocess.run(["bash", script, slot], capture_output=True, text=True, timeout=120)
        state[last_key] = now.isoformat()
        print(f"[content-engine] {slot}: exit={result.returncode}")
        if result.stdout.strip(): print(result.stdout[:300])
        break
pathlib.Path(state_file).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(state_file).write_text(__import__("json").dumps(state, indent=2))
PYEOF2
  fi

  # ── Self-learning slot dispatch ──────────────────────────────────────────────
  SL_STATE="$RICK_DATA_ROOT/logs/self-learning-slots.json"
  SL_SCRIPTS="$ROOT_DIR/skills/self-learning/scripts"
  python3 - "$SL_STATE" "$SL_SCRIPTS" << 'PYEOF_SL'
import sys, json, subprocess, datetime, pathlib

state_file = sys.argv[1]
scripts_dir = pathlib.Path(sys.argv[2])
now = datetime.datetime.now()
today = now.strftime("%Y-%m-%d")
hour = now.hour
weekday = now.strftime("%A")
iso_week = now.strftime("%Y-W%W")

state = {}
try:
    state = json.loads(pathlib.Path(state_file).read_text())
except Exception:
    pass

# Slots in execution-priority order:
# signal_tracker first (feeds others), then revenue_velocity, morning_intel, experiment_engine, prompt_evolution last
slots = []

# 1. content-signal-tracker: every 6h at windows 00,06,12,18
for h in [0, 6, 12, 18]:
    if hour == h:
        slots.append(("signal_tracker", "content-signal-tracker.py",
                      f"{today}_signal_tracker_{h:02d}", True))

# 2. revenue-velocity: every 6h at windows 01,07,13,19
for h in [1, 7, 13, 19]:
    if hour == h:
        slots.append(("revenue_velocity", "revenue-velocity.py",
                      f"{today}_revenue_velocity_{h:02d}", True))

# 3. morning-intelligence: daily 06:00-06:59 (after signal_tracker)
if hour == 6:
    slots.append(("morning_intel", "morning-intelligence.py",
                  f"{today}_morning_intel", True))

# 4. experiment-engine: daily 18:00-18:59
if hour == 18:
    slots.append(("experiment_engine", "experiment-engine.py",
                  f"{today}_experiment_engine", True))

# 5. prompt-evolution: Sunday 20:00-20:59 (always last — reads weekly rollup)
if hour == 20 and weekday == "Sunday":
    slots.append(("prompt_evolution", "prompt-evolution.py",
                  f"{iso_week}_prompt_evolution", True))

# 6. pattern-miner: daily 05:00-05:59 (distills DREAMS + outcomes pareto)
if hour == 5:
    slots.append(("pattern_miner", "pattern-miner.py",
                  f"{today}_pattern_miner", True))

for slot_name, script_name, key, condition in slots:
    if state.get(key):
        continue
    if not condition:
        continue
    script_path = scripts_dir / script_name
    if not script_path.exists():
        print(f"[self-learning] {slot_name}: SKIP (script not found: {script_path})")
        continue
    try:
        result = subprocess.run(
            ["python3", str(script_path)],
            capture_output=True, text=True, timeout=300
        )
        state[key] = now.isoformat()
        status = "ok" if result.returncode == 0 else f"FAIL(exit={result.returncode})"
        print(f"[self-learning] {slot_name}: {status}")
        if result.returncode != 0 and result.stderr:
            print(f"  stderr: {result.stderr[:500]}")
    except subprocess.TimeoutExpired:
        state[key] = now.isoformat()  # mark attempted — prevent retry storm
        print(f"[self-learning] {slot_name}: TIMEOUT (300s)")
    except Exception as e:
        # Don't mark key — allow retry next loop
        print(f"[self-learning] {slot_name}: ERROR ({e})")

pathlib.Path(state_file).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(state_file).write_text(json.dumps(state, indent=2))
PYEOF_SL

  sleep "$INTERVAL_SECONDS"
done
