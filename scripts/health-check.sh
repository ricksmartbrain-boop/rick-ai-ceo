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
TARGETS_FILE="${RICK_HEALTH_TARGETS_FILE:-$HOME/.config/openclaw/health-targets.conf}"
REPORT_FILE="$RICK_DATA_ROOT/control/health-targets-report.md"
VERBOSE=0
NO_ALERT=0
HTTP_TIMEOUT_SECONDS="${RICK_HEALTHCHECK_TIMEOUT_SECONDS:-20}"

usage() {
  cat <<'USAGE'
Usage: scripts/health-check.sh [-t targets-file] [--verbose] [--no-alert]

Targets file format (pipe-delimited):
  url|<name>|<url>|<contains-optional>
  process|<name>|<pgrep-pattern>|<min-uptime-seconds-optional>

Examples:
  url|dashboard|https://meetrick.ai|Healthy
  process|worker|python3 /srv/app/worker.py|300
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--targets)
      TARGETS_FILE="$2"
      shift 2
      ;;
    --verbose)
      VERBOSE=1
      shift
      ;;
    --no-alert)
      NO_ALERT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "$REPORT_FILE")"

log_verbose() {
  if [[ "$VERBOSE" -eq 1 ]]; then
    printf '%s\n' "$*"
  fi
}

report_header() {
  {
    echo "# Health Targets Report"
    echo
    echo "Generated: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Targets file: $TARGETS_FILE"
    echo
    echo "| Name | Kind | Status | Details |"
    echo "|------|------|--------|---------|"
  } > "$REPORT_FILE"
}

append_row() {
  local name="$1"
  local kind="$2"
  local status="$3"
  local details="$4"
  printf '| %s | %s | %s | %s |\n' "$name" "$kind" "$status" "${details//|//}" >> "$REPORT_FILE"
}

send_alert() {
  local text="$1"
  if [[ "$NO_ALERT" -eq 1 ]]; then
    return
  fi
  if command -v openclaw >/dev/null 2>&1; then
    openclaw system event --mode now --text "$text" >/dev/null 2>&1 || true
  fi
}

report_header

if [[ ! -f "$TARGETS_FILE" ]]; then
  append_row "health-targets" "config" "warn" "targets file not configured"
  log_verbose "WARN: targets file not configured: $TARGETS_FILE"
  exit 0
fi

failures=()

record_failure() {
  local name="$1"
  local kind="$2"
  local details="$3"
  failures+=("$kind:$name $details")
  append_row "$name" "$kind" "fail" "$details"
  log_verbose "FAIL: $kind:$name $details"
}

check_url() {
  local name="$1"
  local url="$2"
  local contains="${3:-}"

  local body_file
  body_file="$(mktemp)"

  local code
  code="$(curl -sS -L --connect-timeout 10 --max-time "$HTTP_TIMEOUT_SECONDS" -o "$body_file" -w '%{http_code}' "$url" || true)"

  if [[ "$code" != "200" ]]; then
    record_failure "$name" "url" "status $code ($url)"
    rm -f "$body_file"
    return
  fi

  if [[ -n "$contains" ]] && ! grep -Fq "$contains" "$body_file"; then
    record_failure "$name" "url" "missing expected content [$contains]"
    rm -f "$body_file"
    return
  fi

  append_row "$name" "url" "pass" "ok"
  log_verbose "OK: url:$name"
  rm -f "$body_file"
}

check_process() {
  local name="$1"
  local pattern="$2"
  local min_uptime="${3:-300}"

  local pids
  pids="$(pgrep -f "$pattern" || true)"
  if [[ -z "$pids" ]]; then
    record_failure "$name" "process" "not running (pattern: $pattern)"
    return
  fi

  local uptime_ok=0
  local best_uptime=0
  local pid
  for pid in $pids; do
    local etimes
    etimes="$(ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ' || true)"
    # macOS doesn't support etimes; parse etime directly
    if [[ -n "$etimes" ]] && ! [[ "$etimes" =~ ^[0-9]+$ ]]; then
      # Convert [[DD-]HH:]MM:SS to seconds
      etimes="$(python3 -c "
import re, sys
s = '$etimes'
m = re.match(r'^(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)$', s)
if m:
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3))
    secs = int(m.group(4))
    print(days*86400 + hours*3600 + mins*60 + secs)
else:
    print(0)
" 2>/dev/null || echo 0)"
    fi
    # etime parsing already handled above
    [[ -z "$etimes" ]] && continue
    [[ "$etimes" =~ ^[0-9]+$ ]] || continue
    if (( ${etimes:-0} > ${best_uptime:-0} )); then
      best_uptime="$etimes"
    fi
    if (( ${etimes:-0} >= ${min_uptime:-0} )); then
      uptime_ok=1
    fi
  done

  if (( uptime_ok == 0 )); then
    record_failure "$name" "process" "uptime below threshold (${best_uptime}s < ${min_uptime}s)"
    return
  fi

  append_row "$name" "process" "pass" "uptime ${best_uptime}s"
  log_verbose "OK: process:$name"
}

while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
  line="${raw_line#"${raw_line%%[![:space:]]*}"}"
  [[ -z "$line" || "${line:0:1}" == "#" ]] && continue

  IFS='|' read -r kind name arg3 arg4 <<< "$line"
  kind="$(echo "$kind" | tr '[:upper:]' '[:lower:]' | xargs)"
  name="$(echo "$name" | xargs)"
  arg3="$(echo "${arg3:-}" | xargs)"
  arg4="$(echo "${arg4:-}" | xargs)"

  case "$kind" in
    url)
      [[ -z "$name" || -z "$arg3" ]] && { record_failure "invalid" "url" "malformed target line"; continue; }
      check_url "$name" "$arg3" "$arg4"
      ;;
    process)
      [[ -z "$name" || -z "$arg3" ]] && { record_failure "invalid" "process" "malformed target line"; continue; }
      check_process "$name" "$arg3" "$arg4"
      ;;
    *)
      record_failure "invalid" "config" "unknown target type [$kind]"
      ;;
  esac
done < "$TARGETS_FILE"

if (( ${#failures[@]} > 0 )); then
  summary="rick health-check failed (${#failures[@]} issue(s)): ${failures[*]}"
  send_alert "$summary"
  exit 1
fi

exit 0
