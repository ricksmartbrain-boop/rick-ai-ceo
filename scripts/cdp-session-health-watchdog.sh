#!/usr/bin/env bash
# CDP Session Health Watchdog
# Cron-compatible: checks known Chrome DevTools Protocol ports and alerts ops if any are dead or have zero tabs.

set -u

TG_TOPIC="$HOME/.local/bin/tg-topic"

bad_ports=()

for PORT in 9222 9223 9225; do
  STATUS=$(curl -s --max-time 3 "http://localhost:${PORT}/json" | python3 -c "import sys,json; tabs=json.load(sys.stdin); print(len(tabs),'tabs')" 2>/dev/null || echo 'DEAD')

  echo "Port ${PORT}: ${STATUS}"

  if [ "${STATUS}" = "DEAD" ] || [ "${STATUS}" = "0 tabs" ]; then
    bad_ports+=("${PORT}")
  fi
done

# Reaper (added 2026-07-12): kill leaked Chrome-for-Testing masters older than
# 48h whose user-data-dir is an agent-browser-chrome-* profile. These are
# orphaned agent sessions; KeepAlive'd chrome-cdp browsers use other dirs.
while read -r REAP_PID REAP_ETIME REAP_ARGS; do
  case "${REAP_ARGS}" in
    *Helper*) ;;  # skip helper processes — killing the master reaps them
    *"Google Chrome for Testing"*--user-data-dir=*agent-browser-chrome-*)
      # ps etime is [[dd-]hh:]mm:ss — a day field >= 2 means age >= 48h
      REAP_DAYS="${REAP_ETIME%%-*}"
      if [ "${REAP_DAYS}" != "${REAP_ETIME}" ] && [ "${REAP_DAYS}" -ge 2 ]; then
        echo "Reaping leaked Chrome-for-Testing pid=${REAP_PID} age=${REAP_ETIME}"
        kill "${REAP_PID}" 2>/dev/null
      fi
      ;;
  esac
done < <(ps axo pid=,etime=,args=)

if [ "${#bad_ports[@]}" -gt 0 ]; then
  for PORT in "${bad_ports[@]}"; do
    bash "${TG_TOPIC}" ops-alerts "CDP port ${PORT} is dead — browser session needs restart"
  done
  exit 2
fi

echo "HEARTBEAT_OK"
exit 0
