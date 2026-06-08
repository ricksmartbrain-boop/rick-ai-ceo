#!/usr/bin/env bash
# war-room-data.sh — single-command data pull for the Weekly War Room.
# Avoids brittle chained tool-calls / inline $(date) substitution that crash Codex turns.
# Prints a compact, self-contained snapshot. Read-only.

set -uo pipefail
source ~/clawd/config/rick.env 2>/dev/null || source ~/.openclaw/workspace/config/rick.env 2>/dev/null || true

echo "=== WAR ROOM DATA $(date '+%Y-%m-%d %H:%M %Z') ==="

# --- Stripe: charges in the last 24h ---
SINCE=$(( $(date +%s) - 86400 ))
echo "--- Stripe (last 24h) ---"
if [ -n "${STRIPE_SECRET_KEY:-}" ]; then
  curl -s -G 'https://api.stripe.com/v1/charges' \
    --data-urlencode "created[gte]=${SINCE}" \
    --data-urlencode 'limit=20' \
    -u "${STRIPE_SECRET_KEY}:" 2>/dev/null \
  | python3 -c "
import sys,json,time
try: d=json.load(sys.stdin)
except Exception as e: print('stripe parse error:',e); sys.exit(0)
ch=[c for c in d.get('data',[]) if c.get('status')=='succeeded']
print('charges_24h:',len(ch),'| total_\$:',sum(c['amount'] for c in ch)/100)
for c in ch[:5]:
    print('  ',time.strftime('%Y-%m-%d',time.localtime(c['created'])),'\$'+str(c['amount']/100),(c.get('description') or '')[:40])
" 2>&1
else
  echo "STRIPE_SECRET_KEY not set"
fi

# --- Today's daily note (head) ---
NOTE=~/rick-vault/memory/$(date +%Y-%m-%d).md
echo "--- Daily note: $NOTE ---"
if [ -f "$NOTE" ]; then head -40 "$NOTE"; else echo "(no daily note yet)"; fi

# --- Funnel snapshot (active subs + bounce health) ---
echo "--- Funnel (audience pulse) ---"
python3 ~/.openclaw/workspace/scripts/audience-pulse.py --quiet 2>/dev/null \
  && tail -1 ~/rick-vault/operations/funnel-pulse.jsonl 2>/dev/null \
  | python3 -c "import sys,json; d=json.load(sys.stdin); g=d.get('audiences',{}).get('General',{}); sh=d.get('send_health',{}); print('General active:',g.get('active'),'| recent bounce+suppress %:',sh.get('bounce_suppress_pct'))" 2>&1 \
  || echo "(audience pulse unavailable)"

echo "=== END WAR ROOM DATA ==="
