#!/bin/bash
# Usage: bash tg-topic.sh <topic-name> "message"
# Maps friendly names to thread IDs for both groups

if [ -z "${RICK_TELEGRAM_BOT_TOKEN:-}" ]; then
  for ENV_FILE in "$HOME/.openclaw/workspace/config/rick.env" "$HOME/clawd/config/rick.env"; do
    if [ -f "$ENV_FILE" ]; then
      # shellcheck disable=SC1090
      source "$ENV_FILE"
      break
    fi
  done
fi

BOT_TOKEN="${RICK_TELEGRAM_BOT_TOKEN:?Set RICK_TELEGRAM_BOT_TOKEN}"
TEAM_CHAT="${RICK_TEAM_CHAT_ID:--1003781085932}"
WAR_ROOM="${RICK_WAR_ROOM_CHAT_ID:--1003817549117}"

TOPIC="$1"
MSG="$2"

if [ -z "$MSG" ]; then
  echo "Usage: bash tg-topic.sh <topic> \"message\""
  echo "Topics: ceo-hq, approvals, product-lab, distribution, customer, ops-alerts, traffic, test"
  echo "War Room: ideas, hot-takes, wr-product, war-room, intros, rick-output"
  exit 1
fi

case "$TOPIC" in
  ceo-hq|ceo)       CHAT="$TEAM_CHAT"; TID=24 ;;
  approvals)         CHAT="$TEAM_CHAT"; TID=26 ;;
  product-lab|product) CHAT="$TEAM_CHAT"; TID=28 ;;
  distribution|dist) CHAT="$TEAM_CHAT"; TID=30 ;;
  customer)          CHAT="$TEAM_CHAT"; TID=32 ;;
  ops-alerts|ops)    CHAT="$TEAM_CHAT"; TID=34 ;;
  test)              CHAT="$TEAM_CHAT"; TID=36 ;;
  traffic|analytics) CHAT="$TEAM_CHAT"; TID=715 ;;
  ideas)             CHAT="$WAR_ROOM"; TID=4 ;;
  hot-takes)         CHAT="$WAR_ROOM"; TID=5 ;;
  wr-product)        CHAT="$WAR_ROOM"; TID=6 ;;
  war-room|wr)       CHAT="$WAR_ROOM"; TID=7 ;;
  intros)            CHAT="$WAR_ROOM"; TID=8 ;;
  rick-output|output) CHAT="$WAR_ROOM"; TID=9 ;;
  *)
    echo "Unknown topic: $TOPIC"
    exit 1 ;;
esac

RESULT=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT}" \
  --data-urlencode "message_thread_id=${TID}" \
  --data-urlencode "text=${MSG}" \
  --data-urlencode "parse_mode=Markdown" 2>&1)

OK=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('ok') else 'FAIL: '+d.get('description',''))" 2>/dev/null)
echo "$OK"
