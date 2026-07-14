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

export RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
export RICK_SITES_FILE="${RICK_SITES_FILE:-$ROOT_DIR/config/sites.json}"

REPORT_FILE="$RICK_DATA_ROOT/control/ops-health.md"
TMP_FILE="$(mktemp)"
mkdir -p "$RICK_DATA_ROOT/control"

expand_target() {
  local value="$1"
  value="${value/#\~/$HOME}"
  value="${value//\$RICK_DATA_ROOT/$RICK_DATA_ROOT}"
  value="${value//\$HOME/$HOME}"
  printf '%s' "$value"
}

emit_row() {
  local name="$1"
  local kind="$2"
  local status="$3"
  local details="$4"
  printf '| %s | %s | %s | %s |\n' "$name" "$kind" "$status" "$details" >> "$TMP_FILE"
}

{
  echo "# Ops Health"
  echo
  echo "Generated: $(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "| Name | Kind | Status | Details |"
  echo "|------|------|--------|---------|"
} > "$TMP_FILE"

if [[ ! -f "$RICK_SITES_FILE" ]]; then
  emit_row "Site config" "config" "warn" "missing: $RICK_SITES_FILE"
else
  while IFS=$'\t' read -r name kind target contains; do
    target="$(expand_target "$target")"
    case "$kind" in
      http)
        body=""
        if body="$(curl -fsSL --max-time 8 "$target" 2>/dev/null)"; then
          if [[ -n "$contains" && "$body" != *"$contains"* ]]; then
            emit_row "$name" "$kind" "warn" "response missing expected text"
          else
            emit_row "$name" "$kind" "pass" "ok"
          fi
        else
          emit_row "$name" "$kind" "fail" "request failed"
        fi
        ;;
      path)
        if [[ -e "$target" ]]; then
          emit_row "$name" "$kind" "pass" "$target"
        else
          emit_row "$name" "$kind" "fail" "path missing"
        fi
        ;;
      process)
        if pgrep -f "$target" >/dev/null 2>&1; then
          emit_row "$name" "$kind" "pass" "$target"
        else
          emit_row "$name" "$kind" "fail" "process not found"
        fi
        ;;
      *)
        emit_row "$name" "$kind" "warn" "unknown check kind"
        ;;
    esac
  done < <(jq -r '.checks[] | [.name, .kind, .target, (.contains // "")] | @tsv' "$RICK_SITES_FILE")
fi

mv "$TMP_FILE" "$REPORT_FILE"
echo "$REPORT_FILE"
