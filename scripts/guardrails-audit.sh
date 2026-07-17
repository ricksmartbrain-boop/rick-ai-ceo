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
export RICK_WATCHDOG_PROCESSES_FILE="${RICK_WATCHDOG_PROCESSES_FILE:-$ROOT_DIR/config/watchdog-processes.json}"
export RICK_MEMORY_INDEX_FILE="${RICK_MEMORY_INDEX_FILE:-$RICK_DATA_ROOT/control/memory-index.json}"
export RICK_OPENCLAW_CONFIG_FILE="${RICK_OPENCLAW_CONFIG_FILE:-$HOME/.openclaw/openclaw.json}"

REPORT_FILE="$RICK_DATA_ROOT/control/guardrails-audit.md"
TMP_FILE="$(mktemp)"
mkdir -p "$(dirname "$REPORT_FILE")"

emit_row() {
  local guardrail="$1"
  local status="$2"
  local details="$3"
  printf '| %s | %s | %s |\n' "$guardrail" "$status" "$details" >> "$TMP_FILE"
}

{
  echo "# Guardrails Audit"
  echo
  echo "Generated: $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "| Guardrail | Status | Details |"
  echo "|-----------|--------|---------|"
} > "$TMP_FILE"

if [[ "$RICK_TMUX_SOCKET_PATH" == /tmp/* || "$RICK_TMUX_SOCKET_PATH" == /private/tmp/* ]]; then
  emit_row "Stable tmux socket" "fail" "RICK_TMUX_SOCKET_PATH points to tmp: $RICK_TMUX_SOCKET_PATH"
else
  emit_row "Stable tmux socket" "pass" "$RICK_TMUX_SOCKET_PATH"
fi

# Real Telegram gating lives in the OpenClaw plugin config (allowlist policies),
# not in legacy RICK_TELEGRAM_* env vars — those stay unset live on purpose.
if [[ -f "$RICK_OPENCLAW_CONFIG_FILE" ]] && jq -e '.channels.telegram | (.dmPolicy == "allowlist") and ((.allowFrom | length) > 0) and (.groupPolicy == "allowlist")' "$RICK_OPENCLAW_CONFIG_FILE" >/dev/null 2>&1; then
  emit_row "Founder control gating" "pass" "openclaw.json Telegram dm/group allowlists restrictive with non-empty allowFrom"
else
  emit_row "Founder control gating" "fail" "openclaw.json missing, unparseable, or Telegram allowlist gating not restrictive: $RICK_OPENCLAW_CONFIG_FILE"
fi

if [[ -n "${STRIPE_SECRET_KEY:-}" ]]; then
  emit_row "Revenue path secret" "pass" "Stripe key present"
else
  emit_row "Revenue path secret" "warn" "STRIPE_SECRET_KEY missing"
fi

if [[ -f "$RICK_WATCHDOG_PROCESSES_FILE" ]]; then
  enabled_count="$(jq '[.processes[] | select(.enabled == true)] | length' "$RICK_WATCHDOG_PROCESSES_FILE" 2>/dev/null || echo "0")"
  if [[ "$enabled_count" -gt 0 ]]; then
    emit_row "Managed watchdog registry" "pass" "$enabled_count enabled processes"
  else
    emit_row "Managed watchdog registry" "warn" "no enabled watchdog processes"
  fi
else
  emit_row "Managed watchdog registry" "fail" "missing: $RICK_WATCHDOG_PROCESSES_FILE"
fi

if [[ -f "$RICK_MEMORY_INDEX_FILE" ]]; then
  entry_count="$(jq '.counts.entries // 0' "$RICK_MEMORY_INDEX_FILE" 2>/dev/null || echo "0")"
  emit_row "Indexed memory recall" "pass" "$entry_count indexed entries"
else
  emit_row "Indexed memory recall" "warn" "memory index missing"
fi

if [[ "${RICK_NEWSLETTER_DRAFT_MODE:-}" == "true" ]]; then
  emit_row "Newsletter live mode" "warn" "draft mode enabled"
else
  emit_row "Newsletter live mode" "pass" "draft mode disabled"
fi

if command -v openclaw >/dev/null 2>&1; then
  emit_row "OpenClaw runtime binary" "pass" "$(command -v openclaw)"
else
  emit_row "OpenClaw runtime binary" "warn" "openclaw not found on PATH"
fi

mv "$TMP_FILE" "$REPORT_FILE"
cat "$REPORT_FILE"
