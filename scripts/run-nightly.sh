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

mkdir -p "$RICK_DATA_ROOT/logs"
: >> "$RICK_DATA_ROOT/logs/conversions.log"
chmod +x "$RICK_DATA_ROOT/scripts/fetch-ga-conversions.py" "$RICK_DATA_ROOT/scripts/log-conversion.sh" 2>/dev/null || true

bash "$ROOT_DIR/scripts/bootstrap.sh" >/dev/null
bash "$ROOT_DIR/scripts/doctor.sh" --quiet
bash "$ROOT_DIR/scripts/guardrails-audit.sh" >/dev/null || true
bash "$ROOT_DIR/scripts/health-check.sh" >/dev/null || true
bash "$ROOT_DIR/scripts/sync-to-clawd.sh" || true
bash "$ROOT_DIR/skills/self-healing-ops/scripts/watchdog.sh" || true
python3 "$ROOT_DIR/skills/obsidian-memory/scripts/memory-search.py" stats >/dev/null || true
python3 "$ROOT_DIR/runtime/runner.py" heartbeat --work-limit 4 >/dev/null
python3 "$ROOT_DIR/skills/email-automation/scripts/email-sequence-dispatch.py" >/dev/null || true
python3 "$ROOT_DIR/skills/claude-monitor/scripts/claude-session-digest.py" || true
# stripe-poll failures must be LOUD (2026-07-13): it feeds revenue truth. No silent || true.
if python3 "$ROOT_DIR/scripts/stripe-poll.py" >/dev/null 2>>"$RICK_DATA_ROOT/logs/cron/stripe-poll.err.log"; then
  echo "[ok] stripe-poll"
else
  rc=$?
  echo "[error] stripe-poll FAILED (exit $rc) — see $RICK_DATA_ROOT/logs/cron/stripe-poll.err.log" >&2
fi
python3 "$ROOT_DIR/skills/revenue-dashboard/scripts/revenue-report.py" --period yesterday || true
python3 "$ROOT_DIR/skills/revenue-dashboard/scripts/revenue-cumulative.py" || true
{
  echo ""
  echo "## Conversion Check"
  python3 "$RICK_DATA_ROOT/scripts/fetch-ga-conversions.py" 2>/dev/null || echo "GA unavailable"
  echo ""
  echo "Roast calls from Railway (recent log window):"
  railway logs --limit 50 2>/dev/null | grep -c "POST /roast" || echo "0 roast calls"
} >> "$RICK_DATA_ROOT/memory/$(date +%Y-%m-%d).md"
python3 "$RICK_DATA_ROOT/brain/update.py" --set metrics.roast_calls_today="$(railway logs --limit 100 2>/dev/null | grep -c "POST /roast" || echo 0)" >/dev/null 2>&1 || true
python3 "$ROOT_DIR/scripts/ga4-report.py" --days 1 >> "$RICK_DATA_ROOT/memory/$(date +%Y-%m-%d).md" 2>/dev/null || true
python3 "$ROOT_DIR/scripts/ga4-report.py" --days 7 > "$RICK_DATA_ROOT/dashboards/traffic-report.md" 2>/dev/null || true
python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/rick-exec.py" nightly --write
python3 "$ROOT_DIR/skills/reflection-engine/scripts/daily-retro.py"
python3 "$ROOT_DIR/skills/reflection-engine/scripts/self-growth.py" || true
python3 "$ROOT_DIR/scripts/archive-conversations.py" >/dev/null || true
python3 "$ROOT_DIR/skills/obsidian-memory/scripts/rebuild-memory-index.py" rebuild --write --quiet >/dev/null || true
python3 "$ROOT_DIR/skills/executive-orchestrator/scripts/initiative-scanner.py" || true
python3 "$ROOT_DIR/skills/executive-control/scripts/build-daily-brief.py"
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" record --kind system-run --title "Nightly loop" --status done --area executive-control --project rick-v6 --route analysis --notes "Doctor, guardrails audit, health-target check, watchdog/service check, memory index rebuild, runtime heartbeat/work, email sequence dispatch, claude session digest, revenue report, nightly review, retro, brief, scoreboard refresh." >/dev/null
python3 "$ROOT_DIR/skills/execution-ledger/scripts/execution-ledger.py" summary --write >/dev/null
python3 "$ROOT_DIR/skills/token-economics/scripts/token-usage.py" report --write >/dev/null
python3 "$ROOT_DIR/skills/executive-control/scripts/update-scoreboard.py"

# Nightly workspace snapshot (2026-07-14): local commit only — NEVER pushes.
# cd -P resolves the ~/clawd symlink: the git repo lives at the physical workspace path, not ~/clawd.
# No-ops quietly when nothing changed; failure is LOUD but never blocks the nightly.
SNAP_DIR="$(cd -P "$(dirname "$0")/.." && pwd)"
# Secret-scan gate (2026-07-16): never commit a snapshot whose staged diff adds
# credential-shaped lines. Each literal below is quote-split so the gate cannot
# match its own definition when this script itself is in the staged diff.
# Scans ADDED lines only (-U0, '+' prefix, '+++' headers dropped); grep without
# -q so pipefail never sees a SIGPIPE'd upstream. On hit: abort the snapshot
# LOUDLY, skip the commit, keep running — the gate must never block the nightly.
LEAK_PATTERNS='sk_''live|sk-''ant|sk-''proj|re_[A-Za-z0-9_]{25,}|AI''za|xox[bpsoa]|gh''p_|AK''IA|PRIVATE'' KEY|"cookies":\['
if git -C "$SNAP_DIR" add -A && git -C "$SNAP_DIR" diff-index --quiet --cached HEAD --; then
  echo "[ok] nightly snapshot: no changes"
elif git -C "$SNAP_DIR" diff --cached -U0 | grep -E '^\+' | grep -Ev '^\+\+\+' | grep -E "$LEAK_PATTERNS" >/dev/null; then
  echo "[error] nightly snapshot ABORTED — staged diff matches a secret/leak pattern; NOTHING committed. Inspect 'git -C $SNAP_DIR diff --cached', scrub the secret, and snapshot will resume next night." >&2
elif git -C "$SNAP_DIR" commit -m "nightly snapshot $(date +%F)" >/dev/null; then
  echo "[ok] nightly snapshot: committed"
else
  echo "[error] nightly snapshot FAILED — workspace remains uncommitted; run 'git -C $SNAP_DIR status'" >&2
fi

echo "Nightly run complete."
