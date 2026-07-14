import json, os
from datetime import datetime, timezone, timedelta

STATE_FILE = os.path.expanduser('~/rick-vault/runtime/nurture/state.json')
with open(STATE_FILE) as f:
    state = json.load(f)
now = datetime.now(timezone.utc)
active = 0
due = 0
for email, c in state.get('contacts', {}).items():
    if c.get('status') != 'active':
        continue
    active += 1
    emails_sent = c.get('emails_sent', [])
    next_num = len(emails_sent) + 1
    if next_num > 5:
        continue
    raw_ts = c['enrolled_at']
    if raw_ts.endswith('Z'):
        raw_ts = raw_ts[:-1] + '+00:00'
    enrolled_at = datetime.fromisoformat(raw_ts)
    delay = {1:0,2:24,3:48,4:72,5:168}[next_num]
    due_at = (enrolled_at if enrolled_at.tzinfo else enrolled_at.replace(tzinfo=timezone.utc)) + timedelta(hours=delay)
    if now >= due_at:
        due += 1
print(json.dumps({'active': active, 'due': due}, indent=2))
