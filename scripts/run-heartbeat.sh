#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export ROOT_DIR
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
export RICK_WORKSPACE_ROOT="${RICK_WORKSPACE_ROOT:-$ROOT_DIR}"

bash "$ROOT_DIR/scripts/bootstrap.sh" >/dev/null
bash "$ROOT_DIR/scripts/doctor.sh" --quiet
bash "$ROOT_DIR/scripts/guardrails-audit.sh" >/dev/null || true
bash "$ROOT_DIR/scripts/health-check.sh" >/dev/null || true
bash "$ROOT_DIR/skills/self-healing-ops/scripts/watchdog.sh" || true
bash "$ROOT_DIR/skills/claude-monitor/scripts/system-health.sh" || true
bash "$ROOT_DIR/skills/claude-monitor/scripts/openclaw-health.sh" || true
python3 "$ROOT_DIR/runtime/runner.py" heartbeat --work-limit 2 >/dev/null
bash "$ROOT_DIR/skills/fiverr/scripts/fiverr-monitor.sh" >/dev/null || true
PYTHONPATH="$ROOT_DIR" python3 -c "from runtime.db import connect; from runtime.engine import process_fiverr_inbox; conn = connect(); process_fiverr_inbox(conn); conn.close()" >/dev/null || true
bash "$ROOT_DIR/skills/upwork/scripts/upwork-monitor.sh" >/dev/null || true
if [[ -f "$RICK_DATA_ROOT/upwork/config/rss-feeds.json" ]]; then
  python3 "$ROOT_DIR/skills/upwork/scripts/upwork-rss.py" >/dev/null || true
fi
PYTHONPATH="$ROOT_DIR" python3 -c "from runtime.db import connect; from runtime.engine import process_upwork_inbox; conn = connect(); process_upwork_inbox(conn); conn.close()" >/dev/null || true
python3 "$ROOT_DIR/scripts/stripe-poll.py" >/dev/null || true
python3 "$ROOT_DIR/skills/email-automation/scripts/email-sequence-dispatch.py" >/dev/null || true

TODAY="$(date '+%Y-%m-%d')"
TODAY_FILE="$RICK_DATA_ROOT/memory/$TODAY.md"

if [[ ! -f "$TODAY_FILE" ]]; then
  sed "s/{{date}}/$TODAY/g" "$ROOT_DIR/templates/daily-note.md" > "$TODAY_FILE"
fi

{
  echo
  echo "### $(date '+%H:%M') heartbeat"
  echo "- Doctor refreshed."
  echo "- Guardrails audit refreshed."
  echo "- Health targets, watchdog, and service checks refreshed."
  echo "- System health and OpenClaw health checks refreshed."
  echo "- Executive briefing refreshed."
  echo "- Email sequence dispatch refreshed."
} >> "$TODAY_FILE"

# ── State-diff gate ──────────────────────────────────────────────────────────
# Skip expensive LLM-touching steps (executive briefing, self-learning) when
# the operator has nothing actionable in flight. Cheap local checks above
# always run. Override with RICK_HEARTBEAT_FORCE_HEAVY=1.
HB_STATE_DIR="$RICK_DATA_ROOT/state"
mkdir -p "$HB_STATE_DIR"
HB_LAST_OUTCOME_FILE="$HB_STATE_DIR/heartbeat-last-outcome-id.txt"
PREV_LAST_O=$(cat "$HB_LAST_OUTCOME_FILE" 2>/dev/null || echo "0")
HB_STATE=$(RICK_PREV_LAST_O="$PREV_LAST_O" python3 - << 'PYEOF' 2>/dev/null || echo "ERROR|0|0|0|0"
import os, sqlite3, pathlib
db = pathlib.Path(os.environ.get("RICK_DATA_ROOT", str(pathlib.Path.home()/"rick-vault"))) / "runtime/rick-runtime.db"
if not db.exists():
    print("MISSING|0|0|0|0"); raise SystemExit
con = sqlite3.connect(str(db)); cur = con.cursor()
def q(sql, *a):
    try: return cur.execute(sql, a).fetchone()[0] or 0
    except sqlite3.Error: return 0
prev = int(os.environ.get("RICK_PREV_LAST_O","0") or "0")
qj = q("SELECT COUNT(*) FROM jobs WHERE status='queued'")
oa = q("SELECT COUNT(*) FROM approvals WHERE status='pending'")
last_o = q("SELECT MAX(id) FROM outcomes")
# Real "new business work since last heartbeat" = non-heartbeat outcomes since prev_last_o
nbo = 0
if prev > 0:
    nbo = q("SELECT COUNT(*) FROM outcomes WHERE id > ? AND COALESCE(route,'') NOT IN ('heartbeat','classification') AND COALESCE(workflow_id,'') NOT LIKE 'wf_heartbeat%'", prev)
con.close()
print(f"OK|qj={qj}|oa={oa}|nbo={nbo}|lo={last_o}")
PYEOF
)
HB_QJ=$(echo "$HB_STATE" | grep -oE 'qj=[0-9]+' | head -1 | cut -d= -f2)
HB_OA=$(echo "$HB_STATE" | grep -oE 'oa=[0-9]+' | head -1 | cut -d= -f2)
HB_NBO=$(echo "$HB_STATE" | grep -oE 'nbo=[0-9]+' | head -1 | cut -d= -f2)
HB_LO=$(echo "$HB_STATE" | grep -oE 'lo=[0-9]+' | head -1 | cut -d= -f2)
SKIP_HEAVY=0
if [[ "${HB_QJ:-1}" == "0" && "${HB_OA:-1}" == "0" && "${HB_NBO:-1}" == "0" \
      && "${RICK_HEARTBEAT_FORCE_HEAVY:-0}" != "1" ]]; then
  SKIP_HEAVY=1
fi
[[ -n "$HB_LO" && "$HB_LO" != "0" ]] && echo "$HB_LO" > "$HB_LAST_OUTCOME_FILE" 2>/dev/null || true

if [[ "$SKIP_HEAVY" == "1" ]]; then
  echo "Heartbeat: queue empty + no new business outcomes (qj=$HB_QJ oa=$HB_OA nbo=$HB_NBO) — skipping executive briefing + self-learning."
  echo "" >> "$TODAY_FILE"
  echo "_$(date '+%H:%M') heartbeat: skipped heavy steps (no diff)._" >> "$TODAY_FILE"
else
  python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/rick-exec.py" heartbeat --write
  python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/initiative-scanner.py" --quiet >/dev/null || true
  python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record --kind system-run --title "Heartbeat loop" --status done --area executive-control --project rick-v6 --route heartbeat --notes "Bootstrap, doctor, guardrails audit, health-target check, watchdog/service check, system health, openclaw health, memory index rebuild, runtime heartbeat/work, email sequence dispatch, executive heartbeat, scoreboard refresh." >/dev/null
  python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" summary --write >/dev/null
  python3 "$ROOT_DIR/skills/executive-control/scripts/update-scoreboard.py"

  # ── Self-learning loop status ─────────────────────────────────────────────────
  python3 "$ROOT_DIR/skills/self-learning/scripts/revenue-velocity.py" >/dev/null || true
  python3 "$ROOT_DIR/skills/self-learning/scripts/content-signal-tracker.py" >/dev/null || true
fi

# Write self-learning summary to daily note
{
  echo ""
  echo "#### Self-Learning Loop — $(date '+%H:%M')"
  # Experiment queue status
  ACTIVE_EXP=$(python3 -c "
import json, pathlib
q = pathlib.Path('$RICK_DATA_ROOT/experiments/queue.json')
if q.exists():
    d = json.loads(q.read_text())
    items = d.get('items', [])
    active = [i for i in items if i.get('status') in ('launched','measuring')]
    queued = [i for i in items if i.get('status') == 'queued']
    print(f'Experiments: {len(active)} active, {len(queued)} queued')
else:
    print('Experiments: queue not initialized')
" 2>/dev/null || echo "Experiments: unavailable")
  echo "- $ACTIVE_EXP"
  # Revenue velocity
  VEL=$(python3 -c "
import json, pathlib
v = pathlib.Path('$RICK_DATA_ROOT/revenue/velocity.json')
if v.exists():
    d = json.loads(v.read_text())
    print(f'MRR=\${d.get(\"current_mrr\",0):.0f} velocity={d.get(\"delta_7d\",0):+.0f}/7d flat_days={d.get(\"consecutive_flat_days\",0)}')
else:
    print('velocity: not tracked yet')
" 2>/dev/null || echo "velocity: unavailable")
  echo "- Revenue $VEL"
  # Content signal winner
  SIG=$(python3 -c "
import json, pathlib
t = pathlib.Path('$RICK_DATA_ROOT/projects/x-twitter/signal-tracker.json')
if t.exists():
    d = json.loads(t.read_text())
    rollups = d.get('weekly_rollups', [])
    if rollups:
        bias = rollups[-1].get('queue_bias', {})
        print(f'Content winner: {bias.get(\"winner_type\",\"unknown\")}')
    else:
        print('Content: no rollup yet')
else:
    print('Content: tracker not initialized')
" 2>/dev/null || echo "content: unavailable")
  echo "- $SIG"
} >> "$TODAY_FILE"

# ── Memory sync: promote any new patterns to MEMORY.md ───────────────────────
python3 - << 'PYEOF'
import json, os, pathlib, datetime
DATA_ROOT = pathlib.Path(os.environ.get("RICK_DATA_ROOT", str(pathlib.Path.home() / "rick-vault")))
# ROOT_DIR is exported by the bash script (derived from script location, never corrupted by cron env)
WORKSPACE = pathlib.Path(os.environ.get("ROOT_DIR") or os.environ.get("RICK_WORKSPACE_ROOT") or str(pathlib.Path.home() / ".openclaw" / "workspace"))
MEMORY = WORKSPACE / "MEMORY.md"
patterns_dir = DATA_ROOT / "learning/patterns"
if not patterns_dir.exists():
    exit(0)
# Read patterns promoted today
today = datetime.date.today().isoformat()
new_patterns = [f for f in patterns_dir.glob("*.md") if today in f.read_text()]
if not new_patterns:
    exit(0)
# Append to MEMORY.md
existing = MEMORY.read_text() if MEMORY.exists() else ""
additions = []
for p in new_patterns:
    snippet = p.read_text()[:300].replace('\n', ' ').strip()
    marker = f"[pattern:{p.stem}]"
    if marker not in existing:
        additions.append(f"\n- {marker} {snippet}")
if additions:
    with open(MEMORY, "a") as f:
        f.write(f"\n\n## Auto-Promoted Patterns ({today})\n")
        f.writelines(additions)
    print(f"[heartbeat] Promoted {len(additions)} patterns to MEMORY.md")
PYEOF

echo "Heartbeat complete."
echo "Daily note: $TODAY_FILE"
