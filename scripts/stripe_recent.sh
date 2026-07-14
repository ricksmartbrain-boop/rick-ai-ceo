#!/bin/bash
source ~/clawd/config/rick.env
SINCE=$(python3 -c "import time; print(int(time.time()) - 1800)")
RESULT=$(curl -s -G "https://api.stripe.com/v1/charges" \
  -u "${STRIPE_SECRET_KEY}:" \
  --data-urlencode "limit=5" \
  --data-urlencode "created[gte]=${SINCE}")
echo "$RESULT" | python3 -c "
import json, sys
raw = sys.stdin.read()
if not raw.strip():
    print('no response')
    exit(0)
d = json.loads(raw)
if 'error' in d:
    print('error:', d['error']['message'])
    exit(1)
charges = [c for c in d.get('data', []) if c['status'] == 'succeeded']
print(len(charges), 'new charges')
for c in charges:
    amt = c.get('amount', 0) // 100
    cur = c.get('currency', '').upper()
    email = c.get('receipt_email', 'no email')
    desc = c.get('description', '')
    print(f'  - \${amt} {cur} | {email} | {desc}')
"
