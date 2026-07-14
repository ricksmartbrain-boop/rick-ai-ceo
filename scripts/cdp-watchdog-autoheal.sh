#!/usr/bin/env bash
# CDP Session Health Watchdog + Auto-Heal (deterministic, no LLM in the loop).
#
# Checks each managed CDP Chrome port. If a port is DEAD or returns 0 tabs,
# it kickstarts the owning launchd agent and re-checks. Emits a single
# machine-readable summary line and a nonzero exit only if a port is still
# dead after the heal attempt.
#
# Port -> launchd service map (keep in sync with ~/Library/LaunchAgents):
#   9222 threads   -> ai.meetrick.chrome-cdp-threads
#   9223 reddit    -> ai.meetrick.chrome-cdp-reddit
#   9225 linkedin  -> ai.meetrick.chrome-cdp-linkedin
set -u

# bash 3.2 (macOS default) compatible: no associative arrays.
UID_NUM="$(id -u)"
PORTS="9222 9223 9225"

svc_for_port() {
  case "$1" in
    9222) echo "ai.meetrick.chrome-cdp-threads" ;;
    9223) echo "ai.meetrick.chrome-cdp-reddit" ;;
    9225) echo "ai.meetrick.chrome-cdp-linkedin" ;;
    *) echo "" ;;
  esac
}

probe() {
  # echoes integer tab count, or "DEAD"
  local port="$1"
  curl -s --max-time 4 "http://localhost:${port}/json" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null \
    || echo "DEAD"
}

heal() {
  local svc="$1"
  launchctl kickstart -k "gui/${UID_NUM}/${svc}" >/dev/null 2>&1 \
    || launchctl start "${svc}" >/dev/null 2>&1 \
    || return 1
  return 0
}

still_dead=0
REPORT=""
HEALED=""

for port in $PORTS; do
  count="$(probe "$port")"
  if [ "$count" = "DEAD" ] || [ "$count" = "0" ]; then
    svc="$(svc_for_port "$port")"
    if [ -n "$svc" ]; then
      heal "$svc"
      sleep 6
      count2="$(probe "$port")"
      if [ "$count2" = "DEAD" ] || [ "$count2" = "0" ]; then
        REPORT="${REPORT} port ${port}=${svc}:STILL_DEAD"
        still_dead=1
      else
        REPORT="${REPORT} port ${port}=${svc}:HEALED(${count2}tabs)"
        HEALED="${HEALED} ${port}"
      fi
    else
      REPORT="${REPORT} port ${port}=UNMAPPED:DEAD"
      still_dead=1
    fi
  else
    REPORT="${REPORT} port ${port}=ok(${count}tabs)"
  fi
done

echo "CDP_WATCHDOG${REPORT}"
if [ -n "${HEALED// /}" ]; then
  echo "HEALED_PORTS=${HEALED# }"
fi
if [ "$still_dead" -eq 1 ]; then
  echo "STATUS=ALERT_PORTS_STILL_DEAD"
  exit 1
fi
echo "STATUS=OK"
exit 0
