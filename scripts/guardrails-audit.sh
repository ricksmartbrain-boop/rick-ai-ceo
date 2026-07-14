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
export RICK_DEFAULT_WAITLIST_API="${RICK_DEFAULT_WAITLIST_API:-}"

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

if [[ -n "${RICK_PRIMARY_DOMAIN:-}" ]] && [[ "${RICK_PRIMARY_DOMAIN:-}" != *example.com* ]] && [[ "${RICK_PRIMARY_DOMAIN:-}" != *invalid* ]]; then
  emit_row "Primary domain" "pass" "${RICK_PRIMARY_DOMAIN}"
else
  emit_row "Primary domain" "warn" "unset or placeholder domain"
fi

if [[ -n "${RICK_TELEGRAM_ALLOWED_CHAT_ID:-}" ]] && [[ -n "${RICK_TELEGRAM_BOT_TOKEN:-}" ]]; then
  emit_row "Founder control gating" "pass" "Telegram bot token and allowed chat are configured"
else
  emit_row "Founder control gating" "fail" "Telegram founder control is incomplete"
fi

if [[ -n "${STRIPE_SECRET_KEY:-}" ]]; then
  emit_row "Revenue path secret" "pass" "Stripe key present"
else
  emit_row "Revenue path secret" "warn" "STRIPE_SECRET_KEY missing"
fi

if [[ -n "$RICK_DEFAULT_WAITLIST_API" ]] && [[ "$RICK_DEFAULT_WAITLIST_API" =~ ^https?:// ]] && [[ "$RICK_DEFAULT_WAITLIST_API" != *invalid* ]] && [[ "$RICK_DEFAULT_WAITLIST_API" != *example.com* ]] && [[ "$RICK_DEFAULT_WAITLIST_API" != *example.org* ]] && [[ "$RICK_DEFAULT_WAITLIST_API" != *example.net* ]]; then
  emit_row "Waitlist fallback path" "pass" "$RICK_DEFAULT_WAITLIST_API"
else
  emit_row "Waitlist fallback path" "warn" "unset or placeholder; products without checkout will block before launch"
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

if [[ -f "$RICK_DATA_ROOT/control/founder-profile.md" ]] && ! grep -q '\[TODO' "$RICK_DATA_ROOT/control/founder-profile.md"; then
  emit_row "Founder profile completeness" "pass" "founder-profile.md filled"
else
  emit_row "Founder profile completeness" "warn" "founder-profile.md still has placeholders"
fi

mv "$TMP_FILE" "$REPORT_FILE"
cat "$REPORT_FILE"
