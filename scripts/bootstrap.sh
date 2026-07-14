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
export RICK_TMUX_SOCKET_PATH="${RICK_TMUX_SOCKET_PATH:-$HOME/.tmux/sock}"
export RICK_OPENCLAW_MAIN_AGENT_ID="${RICK_OPENCLAW_MAIN_AGENT_ID:-rick}"
export RICK_PORTFOLIO_SCORECARDS_FILE="${RICK_PORTFOLIO_SCORECARDS_FILE:-$RICK_DATA_ROOT/scorecards/portfolio.json}"
export RICK_EXECUTION_LEDGER_FILE="${RICK_EXECUTION_LEDGER_FILE:-$RICK_DATA_ROOT/operations/execution-ledger.jsonl}"
export RICK_LLM_USAGE_LOG_FILE="${RICK_LLM_USAGE_LOG_FILE:-$RICK_DATA_ROOT/operations/llm-usage.jsonl}"
export RICK_RUNTIME_DB_FILE="${RICK_RUNTIME_DB_FILE:-$RICK_DATA_ROOT/runtime/rick-runtime.db}"
export RICK_STRIPE_ACCOUNTS_FILE="${RICK_STRIPE_ACCOUNTS_FILE:-$ROOT_DIR/config/stripe-accounts.json}"
export RICK_SITES_FILE="${RICK_SITES_FILE:-$ROOT_DIR/config/sites.json}"
export RICK_APPROVAL_POLICY_FILE="${RICK_APPROVAL_POLICY_FILE:-$ROOT_DIR/config/approval-policy.json}"
export RICK_LANE_POLICY_FILE="${RICK_LANE_POLICY_FILE:-$ROOT_DIR/config/lane-policy.json}"
export RICK_WATCHDOG_PROCESSES_FILE="${RICK_WATCHDOG_PROCESSES_FILE:-$ROOT_DIR/config/watchdog-processes.json}"
export RICK_TELEGRAM_TOPICS_FILE="${RICK_TELEGRAM_TOPICS_FILE:-$ROOT_DIR/config/telegram-topics.json}"
export RICK_TOKEN_BUDGET_FILE="${RICK_TOKEN_BUDGET_FILE:-$ROOT_DIR/config/token-budgets.json}"
export RICK_MODEL_PRICING_FILE="${RICK_MODEL_PRICING_FILE:-$ROOT_DIR/config/model-pricing.json}"
export RICK_PORTFOLIO_FILE="${RICK_PORTFOLIO_FILE:-$ROOT_DIR/config/portfolio.json}"
export RICK_OPENCLAW_SESSION_POLICY_FILE="${RICK_OPENCLAW_SESSION_POLICY_FILE:-$ROOT_DIR/config/openclaw-session-policy.json}"
export RICK_OPENCLAW_AGENT_BLUEPRINT_FILE="${RICK_OPENCLAW_AGENT_BLUEPRINT_FILE:-$ROOT_DIR/config/openclaw-agent-blueprint.json}"
export RICK_OPENCLAW_MEMORY_FLUSH_PROMPT_FILE="${RICK_OPENCLAW_MEMORY_FLUSH_PROMPT_FILE:-$ROOT_DIR/templates/openclaw/memory-flush.prompt.md}"
export RICK_OPENCLAW_SECURE_DM_MODE="${RICK_OPENCLAW_SECURE_DM_MODE:-prepared}"
export RICK_MEMORY_INDEX_FILE="${RICK_MEMORY_INDEX_FILE:-$RICK_DATA_ROOT/control/memory-index.json}"
export RICK_MEMORY_OVERVIEW_FILE="${RICK_MEMORY_OVERVIEW_FILE:-$RICK_DATA_ROOT/dashboards/memory-overview.md}"

mkdir -p "$(dirname "$RICK_TMUX_SOCKET_PATH")"

bash "$ROOT_DIR/skills/obsidian-memory/scripts/init-rick-workspace.sh"

copy_example_if_missing() {
  local target="$1"
  local example="$2"
  if [[ ! -f "$target" && -f "$example" ]]; then
    mkdir -p "$(dirname "$target")"
    cp "$example" "$target"
  fi
}

mkdir -p \
  "$RICK_DATA_ROOT/control/briefings" \
  "$RICK_DATA_ROOT/control/morning-briefs" \
  "$RICK_DATA_ROOT/dashboards" \
  "$RICK_DATA_ROOT/reflections/daily" \
  "$RICK_DATA_ROOT/reflections/weekly" \
  "$RICK_DATA_ROOT/operations" \
  "$RICK_DATA_ROOT/runtime" \
  "$RICK_DATA_ROOT/logs" \
  "$RICK_DATA_ROOT/scorecards"

if [[ ! -f "$RICK_DATA_ROOT/control/approvals.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/approvals.md"
# Approvals

| Date | Status | Owner | Area | Request | Impact |
|------|--------|-------|------|---------|--------|
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/dependency-gaps.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/dependency-gaps.md"
# Dependency Gaps

No gaps recorded yet.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/risk-register.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/risk-register.md"
# Risk Register

| Date | Severity | Area | Risk | Mitigation |
|------|----------|------|------|------------|
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/ops-health.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/ops-health.md"
# Ops Health

No checks run yet.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/recovery-log.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/recovery-log.md"
# Recovery Log

No recovery actions recorded yet.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/watchdog-report.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/watchdog-report.md"
# Watchdog Report

Run `bash skills/self-healing-ops/scripts/watchdog.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/health-targets-report.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/health-targets-report.md"
# Health Targets Report

Run `bash scripts/health-check.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/guardrails-audit.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/guardrails-audit.md"
# Guardrails Audit

Run `bash scripts/guardrails-audit.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/telegram-topics.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/telegram-topics.md"
# Telegram Topics

Run `python3 runtime/runner.py telegram-topics list` after bootstrap or `python3 runtime/runner.py telegram-topics bootstrap` in thread mode to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/openclaw-profile.md" ]]; then
  if [[ -f "$ROOT_DIR/OPENCLAW_PROFILE.md" ]]; then
    cp "$ROOT_DIR/OPENCLAW_PROFILE.md" "$RICK_DATA_ROOT/control/openclaw-profile.md"
  else
    cat <<'EOF' > "$RICK_DATA_ROOT/control/openclaw-profile.md"
# Rick OpenClaw Profile

Review `OPENCLAW_PROFILE.md` in the workspace for the single-agent-now / four-agent-later design.
EOF
  fi
fi

for template_name in founder-profile access-inventory heartbeat-targets launch-playbook; do
  target_file="$RICK_DATA_ROOT/control/${template_name}.md"
  template_file="$ROOT_DIR/templates/control/${template_name}.md"
  if [[ ! -f "$target_file" ]] && [[ -f "$template_file" ]]; then
    cp "$template_file" "$target_file"
  fi
done

if [[ ! -f "$RICK_DATA_ROOT/dashboards/scoreboard.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/scoreboard.md"
# Scoreboard

Run `bash scripts/run-heartbeat.sh` or `bash scripts/run-nightly.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/dashboards/execution-ledger.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/execution-ledger.md"
# Execution Ledger

Run `python3 skills/execution-ledger/scripts/execution-ledger.py summary --write` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/dashboards/token-economics.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/token-economics.md"
# Token Economics

Run `python3 skills/token-economics/scripts/token-usage.py report --write` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/dashboards/runtime-status.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/runtime-status.md"
# Runtime Status

Run `python3 runtime/runner.py status` to inspect workflow, job, and approval state.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/system-health.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/system-health.md"
# System Health

Run `bash skills/claude-monitor/scripts/system-health.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/control/openclaw-status.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/control/openclaw-status.md"
# OpenClaw Status

Run `bash skills/claude-monitor/scripts/openclaw-health.sh` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/dashboards/claude-sessions.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/claude-sessions.md"
# Claude Sessions

Run `python3 skills/claude-monitor/scripts/claude-session-digest.py` to populate this file.
EOF
fi

if [[ ! -f "$RICK_DATA_ROOT/dashboards/log-anomalies.md" ]]; then
  cat <<'EOF' > "$RICK_DATA_ROOT/dashboards/log-anomalies.md"
# Log Anomalies

Run `python3 skills/claude-monitor/scripts/log-anomaly-digest.py` to populate this file.
EOF
fi

if [[ ! -f "$RICK_MEMORY_OVERVIEW_FILE" ]]; then
  cat <<'EOF' > "$RICK_MEMORY_OVERVIEW_FILE"
# Memory Overview

Run `python3 skills/obsidian-memory/scripts/rebuild-memory-index.py rebuild --write` to populate this file.
EOF
fi

if [[ ! -f "$RICK_PORTFOLIO_SCORECARDS_FILE" ]]; then
  if [[ -f "$ROOT_DIR/config/portfolio-scorecards.example.json" ]]; then
    mkdir -p "$(dirname "$RICK_PORTFOLIO_SCORECARDS_FILE")"
    cp "$ROOT_DIR/config/portfolio-scorecards.example.json" "$RICK_PORTFOLIO_SCORECARDS_FILE"
  fi
fi

copy_example_if_missing "$RICK_STRIPE_ACCOUNTS_FILE" "$ROOT_DIR/config/stripe-accounts.example.json"
copy_example_if_missing "$RICK_SITES_FILE" "$ROOT_DIR/config/sites.example.json"
copy_example_if_missing "$RICK_APPROVAL_POLICY_FILE" "$ROOT_DIR/config/approval-policy.example.json"
copy_example_if_missing "$RICK_LANE_POLICY_FILE" "$ROOT_DIR/config/lane-policy.example.json"
copy_example_if_missing "$RICK_WATCHDOG_PROCESSES_FILE" "$ROOT_DIR/config/watchdog-processes.example.json"
copy_example_if_missing "$RICK_TELEGRAM_TOPICS_FILE" "$ROOT_DIR/config/telegram-topics.example.json"
copy_example_if_missing "$RICK_TOKEN_BUDGET_FILE" "$ROOT_DIR/config/token-budgets.example.json"
copy_example_if_missing "$RICK_MODEL_PRICING_FILE" "$ROOT_DIR/config/model-pricing.example.json"
copy_example_if_missing "$RICK_PORTFOLIO_FILE" "$ROOT_DIR/config/portfolio.example.json"
copy_example_if_missing "$RICK_OPENCLAW_SESSION_POLICY_FILE" "$ROOT_DIR/config/openclaw-session-policy.example.json"
copy_example_if_missing "$RICK_OPENCLAW_AGENT_BLUEPRINT_FILE" "$ROOT_DIR/config/openclaw-agent-blueprint.example.json"

python3 "$ROOT_DIR/runtime/runner.py" init >/dev/null
python3 "$ROOT_DIR/skills/obsidian-memory/scripts/rebuild-memory-index.py" rebuild --write --quiet >/dev/null || true

echo "Rick v6 bootstrap complete."
echo "Vault: $RICK_DATA_ROOT"
