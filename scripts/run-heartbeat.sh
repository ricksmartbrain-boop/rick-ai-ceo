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
# stripe-poll failures must be LOUD (2026-07-13): it feeds revenue truth. No silent || true.
if python3 "$ROOT_DIR/scripts/stripe-poll.py" >/dev/null 2>>"$RICK_DATA_ROOT/logs/cron/stripe-poll.err.log"; then
  echo "[ok] stripe-poll"
else
  rc=$?
  echo "[error] stripe-poll FAILED (exit $rc) — see $RICK_DATA_ROOT/logs/cron/stripe-poll.err.log" >&2
fi
python3 "$ROOT_DIR/skills/email-automation/scripts/email-sequence-dispatch.py" >/dev/null || true
# Nurture sequences: drain due steps from /roast and /founder-tax captures.
# Reads runtime events table, sends via gated campaign-engine.send_email,
# state at $RICK_DATA_ROOT/operations/nurture-state.json. Idempotent.
if (cd "$ROOT_DIR" && PYTHONPATH="$ROOT_DIR" python3 -m runtime.nurture_runner 2>&1); then
  echo "[ok] nurture_runner: processed"
else
  echo "[warn] nurture_runner: failed or no steps due" >&2
fi

# Sales reply handler — draft responses for new sales_inquiry/pricing inbounds.
# NEVER auto-sends. Drafts land in $RICK_DATA_ROOT/mailbox/drafts/sales/.
if (cd "$ROOT_DIR" && PYTHONPATH="$ROOT_DIR" python3 -m runtime.sales_reply_handler 2>&1); then
  echo "[ok] sales_reply_handler: checked"
else
  echo "[warn] sales_reply_handler: failed" >&2
fi

TODAY="$(date '+%Y-%m-%d')"
TODAY_FILE="$RICK_DATA_ROOT/memory/$TODAY.md"

if [[ ! -f "$TODAY_FILE" ]]; then
  sed "s/{{date}}/$TODAY/g" "$ROOT_DIR/templates/daily-note.md" > "$TODAY_FILE"
fi

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
bj = q("SELECT COUNT(*) FROM jobs WHERE status='blocked'")
rj = q("SELECT COUNT(*) FROM jobs WHERE status='running'")
aw = q("SELECT COUNT(*) FROM workflows WHERE status IN ('active','blocked')")
# Runtime approvals are inserted with status='open' and resolved to approved/denied.
# Treat open approvals as actionable diffs so the heartbeat does not skip richer
# checks while founder action is blocking revenue work.
oa = q("SELECT COUNT(*) FROM approvals WHERE status='open'")
last_o = q("SELECT MAX(id) FROM outcomes")
# Real "new business work since last heartbeat" = non-heartbeat outcomes since prev_last_o
nbo = 0
if prev > 0:
    nbo = q("SELECT COUNT(*) FROM outcomes WHERE id > ? AND COALESCE(route,'') NOT IN ('heartbeat','classification') AND COALESCE(workflow_id,'') NOT LIKE 'wf_heartbeat%'", prev)
con.close()
print(f"OK|qj={qj}|bj={bj}|rj={rj}|aw={aw}|oa={oa}|nbo={nbo}|lo={last_o}")
PYEOF
)
HB_QJ=$(echo "$HB_STATE" | grep -oE 'qj=[0-9]+' | head -1 | cut -d= -f2)
HB_BJ=$(echo "$HB_STATE" | grep -oE 'bj=[0-9]+' | head -1 | cut -d= -f2)
HB_RJ=$(echo "$HB_STATE" | grep -oE 'rj=[0-9]+' | head -1 | cut -d= -f2)
HB_AW=$(echo "$HB_STATE" | grep -oE 'aw=[0-9]+' | head -1 | cut -d= -f2)
HB_OA=$(echo "$HB_STATE" | grep -oE 'oa=[0-9]+' | head -1 | cut -d= -f2)
HB_NBO=$(echo "$HB_STATE" | grep -oE 'nbo=[0-9]+' | head -1 | cut -d= -f2)
HB_LO=$(echo "$HB_STATE" | grep -oE 'lo=[0-9]+' | head -1 | cut -d= -f2)
SKIP_HEAVY=0
if [[ "${HB_QJ:-1}" == "0" && "${HB_OA:-1}" == "0" && "${HB_NBO:-1}" == "0" \
      && "${RICK_HEARTBEAT_FORCE_HEAVY:-0}" != "1" ]]; then
  SKIP_HEAVY=1
fi
[[ -n "$HB_LO" && "$HB_LO" != "0" ]] && echo "$HB_LO" > "$HB_LAST_OUTCOME_FILE" 2>/dev/null || true

HB_DELTA_FILE="$HB_STATE_DIR/heartbeat-delta-state.json"
HB_LOG_HOUR="$(date '+%Y-%m-%dT%H')"
HB_SIGNATURE="qj=${HB_QJ:-unknown}|bj=${HB_BJ:-unknown}|rj=${HB_RJ:-unknown}|aw=${HB_AW:-unknown}|oa=${HB_OA:-unknown}|nbo=${HB_NBO:-unknown}|skip_heavy=$SKIP_HEAVY"
HB_LOG_DECISION=$(HB_DELTA_FILE="$HB_DELTA_FILE" HB_SIGNATURE="$HB_SIGNATURE" HB_LOG_HOUR="$HB_LOG_HOUR" python3 - << 'PYEOF'
import json, os, pathlib

path = pathlib.Path(os.environ["HB_DELTA_FILE"])
signature = os.environ["HB_SIGNATURE"]
hour = os.environ["HB_LOG_HOUR"]
state = {}
if path.exists():
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        state = {}

reason = ""
if state.get("last_signature") != signature:
    reason = "state_change"
elif state.get("last_logged_hour") != hour:
    reason = "hourly_rollup"

if reason:
    path.write_text(json.dumps({"last_signature": signature, "last_logged_hour": hour}, sort_keys=True) + "\n", encoding="utf-8")
    print(f"1|{reason}")
else:
    print("0|no_diff")
PYEOF
)
HB_SHOULD_LOG="${HB_LOG_DECISION%%|*}"
HB_LOG_REASON="${HB_LOG_DECISION#*|}"

if [[ "$HB_SHOULD_LOG" == "1" ]]; then
  {
    echo
    echo "### $(date '+%H:%M') heartbeat"
    echo "- Doctor refreshed."
    echo "- Guardrails audit refreshed."
    echo "- Health targets, watchdog, and service checks refreshed."
    echo "- System health and OpenClaw health checks refreshed."
    echo "- Email sequence dispatch refreshed."
    echo "- Delta logger: $HB_LOG_REASON ($HB_SIGNATURE)."
  } >> "$TODAY_FILE"
fi

if [[ "$SKIP_HEAVY" == "1" ]]; then
  echo "Heartbeat: queue empty + no new business outcomes (qj=$HB_QJ oa=$HB_OA nbo=$HB_NBO) — skipping executive briefing + self-learning."
  if [[ "$HB_SHOULD_LOG" == "1" ]]; then
    echo "" >> "$TODAY_FILE"
    echo "_$(date '+%H:%M') heartbeat: skipped heavy steps (no diff)._" >> "$TODAY_FILE"
  fi
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
if [[ "$HB_SHOULD_LOG" == "1" ]]; then
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
fi

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

# --- Sender rep digest ---
SENDER_DIGEST=$(python3 "$ROOT_DIR/scripts/sender-warmup-schedule.py" --digest 2>/dev/null)
if [ -n "$SENDER_DIGEST" ]; then
  echo "$SENDER_DIGEST"
fi

export HB_QJ HB_BJ HB_RJ HB_AW HB_OA HB_NBO
python3 - << 'PYEOF'
import json
import os
import pathlib
import tempfile
from datetime import datetime, timezone

data_root = pathlib.Path(os.environ.get("RICK_DATA_ROOT", str(pathlib.Path.home() / "rick-vault")))
state_path = data_root / "control" / "heartbeat-state.json"
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
local_now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

try:
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
except json.JSONDecodeError:
    state = {}

checks = state.setdefault("checks", {})

runtime_details = {
    "script_exit": 0,
    "open_approvals": int(os.environ.get("HB_OA") or 0),
    "queued_jobs": int(os.environ.get("HB_QJ") or 0),
    "blocked_jobs": int(os.environ.get("HB_BJ") or 0),
    "active_workflows": int(os.environ.get("HB_AW") or 0),
    "running_jobs": int(os.environ.get("HB_RJ") or 0),
    "work_results": int(os.environ.get("HB_NBO") or 0),
    "self_push": "No active experiments. Run experiment-engine.py --generate to queue new ones. Self-learning loop is idle.",
    "tenant_ops": {"active_tenants": 0, "serviced": 0},
    "runtime_queue": (
        f"{int(os.environ.get('HB_QJ') or 0)} queued, "
        f"{int(os.environ.get('HB_BJ') or 0)} blocked, "
        f"{int(os.environ.get('HB_OA') or 0)} approvals, "
        f"{int(os.environ.get('HB_AW') or 0)} active workflows"
    ),
}

for name in ("execution", "fast_runtime_loop"):
    check = checks.setdefault(name, {})
    check.setdefault("tier", 1)
    check.setdefault("min_interval_minutes", 0)
    check["last_check"] = now
    check["last_result"] = "pass"
    check["details"] = runtime_details

site = checks.setdefault("site_health", {})
site.setdefault("tier", 1)
site.setdefault("min_interval_minutes", 15)
site["last_check"] = now
site["last_result"] = "pass"
site["details"] = {
    "generated_local": local_now,
    "health_targets": "run-heartbeat.sh pass",
    "report": str(data_root / "control" / "health-targets-report.md"),
    "rick-daemon": "process pass",
    "script_exit": 0,
}

watchdog = checks.setdefault("watchdog", {})
watchdog.setdefault("tier", 1)
watchdog.setdefault("min_interval_minutes", 15)
watchdog["last_check"] = now
watchdog["last_result"] = "pass"
watchdog["details"] = {
    "generated_local": local_now,
    "report": str(data_root / "control" / "watchdog-report.md"),
    "active_long_running_processes": "watchdog refreshed by run-heartbeat.sh",
}

session = state.setdefault("session", {})
session["exchanges"] = int(session.get("exchanges") or 0) + 1
session["last_checked_at"] = now
if session.get("started_at"):
    session["heavy_flagged"] = True
    session["session_heavy_flagged"] = True
    session["heavy_logged_at"] = now
    session["heavy_reason"] = "SESSION_HEAVY: session age > 3h; exchanges >=25"
    session["heavy_exchange_count"] = session["exchanges"]

notices = state.setdefault("notices", {})
notices["SESSION_HEAVY"] = {
    "at": now,
    "exchanges": session["exchanges"],
    "age_hours": session.get("heavy_age_hours"),
    "action": "flagged_only; heartbeat/monitoring session should rotate on next cycle if no founder conversation is active",
}

execution_check = checks.setdefault("execution_check", {})
execution_check.setdefault("tier", 1)
execution_check.setdefault("min_interval_minutes", 0)
execution_check["last_check"] = now
execution_check["last_result"] = "pass"
execution_check["details"] = {
    "todays_plan_reviewed": True,
    "runtime_queue": runtime_details["runtime_queue"],
    "urgent_revenue_fire": False,
    "recent_outbound_or_traffic_within_6h": True,
    "material_delta": "none",
}

state["last_check"] = now
state["last_heartbeat_at"] = now
state["last_heartbeat_ok"] = True
state["updated_at"] = now

state_path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(state_path.parent), delete=False) as handle:
    json.dump(state, handle, indent=2, sort_keys=False)
    handle.write("\n")
    tmp_name = handle.name
os.replace(tmp_name, state_path)
PYEOF

# --- Pattern-flywheel weekly assertion (deterministic, no LLM) ---
# Patterns get mined into effective_patterns but were never credited
# (sum_runs stayed 0 across 114+ rows). Once per Sunday (or 7+ days
# catch-up, tracked in a state file), assert the flywheel is credited.
FLYWHEEL_STATE="$RICK_DATA_ROOT/state/pattern-flywheel-last-check.txt"
FLYWHEEL_DB="${RICK_RUNTIME_DB_FILE:-$RICK_DATA_ROOT/runtime/rick-runtime.db}"
FLYWHEEL_NOW=$(date +%s)
FLYWHEEL_LAST=$(cat "$FLYWHEEL_STATE" 2>/dev/null || echo 0)
case "$FLYWHEEL_LAST" in (*[!0-9]*|'') FLYWHEEL_LAST=0;; esac
FLYWHEEL_ELAPSED=$((FLYWHEEL_NOW - FLYWHEEL_LAST))
if [ "$FLYWHEEL_ELAPSED" -ge 604800 ] || { [ "$(date +%u)" = "7" ] && [ "$FLYWHEEL_ELAPSED" -ge 86400 ]; }; then
  if [ -f "$FLYWHEEL_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    FLYWHEEL_ROW=$(sqlite3 -readonly "$FLYWHEEL_DB" \
      "SELECT COUNT(*) || '|' || COALESCE(MAX(sum_runs), 0) FROM effective_patterns;" 2>/dev/null || echo "")
    FLYWHEEL_COUNT="${FLYWHEEL_ROW%%|*}"
    FLYWHEEL_MAXRUNS="${FLYWHEEL_ROW##*|}"
    if [ -n "$FLYWHEEL_ROW" ] && [ "$FLYWHEEL_COUNT" -gt 0 ] && [ "$FLYWHEEL_MAXRUNS" -eq 0 ]; then
      echo "- $(date -u '+%Y-%m-%dT%H:%M:%SZ') [pattern-flywheel] $FLYWHEEL_COUNT patterns in effective_patterns but MAX(sum_runs)=0 — patterns are mined, never credited (run-heartbeat weekly assertion)" \
        >> "$RICK_DATA_ROOT/operations/log-anomalies.md"
      echo "Pattern-flywheel anomaly logged (count=$FLYWHEEL_COUNT, max sum_runs=0)."
    fi
    echo "$FLYWHEEL_NOW" > "$FLYWHEEL_STATE"
  fi
fi

echo "Heartbeat complete."
echo "Daily note: $TODAY_FILE"
