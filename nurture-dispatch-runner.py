#!/usr/bin/env python3
import json, os, sys, logging, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nurture_emails import EMAIL_FUNCS, EMAIL_DELAYS_HOURS

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request

STATE_FILE = os.environ['STATE_FILE']
SENT_LOG = os.environ['SENT_LOG']
LOG_FILE = os.environ.get('LOG_FILE', str(Path(__file__).with_suffix('.log')))
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'Rick <rick@meetrick.ai>')
REPLY_TO = os.environ.get('REPLY_TO', 'rick@meetrick.ai')
RESEND_URL = 'https://api.resend.com/emails'

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger('nurture-dispatch')


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def idem(email, n):
    return hashlib.sha256(f'nurture-v1:{email}:{n}'.encode()).hexdigest()[:16]


def load_sent():
    sent = set()
    if os.path.exists(SENT_LOG):
        with open(SENT_LOG) as f:
            for line in f:
                if line.strip():
                    sent.add(line.split('\t', 1)[0])
    return sent


def record_sent(k, email, n):
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    with open(SENT_LOG, 'a') as f:
        f.write(f'{k}\t{email}\t{n}\t{datetime.now(timezone.utc).isoformat()}\n')


def send_email(to_email, subject, html_body):
    payload = {'from': FROM_EMAIL, 'to': [to_email], 'reply_to': REPLY_TO, 'subject': subject, 'html': html_body}
    headers = {'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'}
    if not RESEND_API_KEY:
        log.error('RESEND_API_KEY missing')
        return False
    try:
        if HAS_REQUESTS:
            resp = requests.post(RESEND_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                log.info('Sent to %s: %s (id: %s)', to_email, subject, resp.json().get('id', '?'))
                return True
            log.error('Resend error %d for %s: %s', resp.status_code, to_email, resp.text)
            return False
        data = json.dumps(payload).encode()
        req = urllib.request.Request(RESEND_URL, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
            log.info('Sent to %s: %s (id: %s)', to_email, subject, body.get('id', '?'))
            return True
    except Exception as e:
        log.error('Failed to send to %s: %s', to_email, e)
        return False


def extract_name(email):
    local = email.split('@')[0]
    for prefix in ['info', 'hello', 'contact', 'admin', 'support', 'team', 'office', 'help']:
        if local.lower() == prefix:
            return 'there'
    name = local.split('.')[0].split('_')[0].split('-')[0]
    return 'there' if len(name) < 2 or name.isdigit() else name.capitalize()


state = load_state()
log.info('=== Nurture dispatch run ===')
log.info('Active contacts: %d', sum(1 for c in state.get('contacts', {}).values() if c.get('status') == 'active'))
now = datetime.now(timezone.utc)
sent_set = load_sent()
sent = skipped = errors = 0

for email, contact in state.get('contacts', {}).items():
    if contact.get('status') != 'active':
        continue
    if email in set(state.get('unsubscribed', [])):
        contact['status'] = 'unsubscribed'
        continue
    emails_sent = contact.get('emails_sent', [])
    next_num = len(emails_sent) + 1
    if next_num > 5:
        contact['status'] = 'completed'
        continue
    raw_ts = contact['enrolled_at']
    if raw_ts.endswith('Z'):
        raw_ts = raw_ts[:-1] + '+00:00'
    enrolled = datetime.fromisoformat(raw_ts)
    due_at = (enrolled.replace(tzinfo=timezone.utc) if enrolled.tzinfo is None else enrolled) + timedelta(hours=EMAIL_DELAYS_HOURS[next_num])
    if now < due_at:
        skipped += 1
        continue
    k = idem(email, next_num)
    if k in sent_set:
        if next_num not in emails_sent:
            emails_sent.append(next_num)
            contact['emails_sent'] = emails_sent
        log.info('Already sent email %d to %s (idempotency), fixing state', next_num, email)
        continue
    url = contact.get('url', 'your site')
    first_name = contact.get('first_name') or extract_name(email)
    subject, html_body = EMAIL_FUNCS[next_num](first_name, url)
    if send_email(email, subject, html_body):
        emails_sent.append(next_num)
        contact['emails_sent'] = emails_sent
        contact['last_sent_at'] = now.isoformat()
        contact['next_due_at'] = None
        if next_num < 5:
            contact['next_due_at'] = (enrolled + timedelta(hours=EMAIL_DELAYS_HOURS[next_num + 1])).isoformat()
        else:
            contact['status'] = 'completed'
        record_sent(k, email, next_num)
        sent += 1
    else:
        errors += 1

save_state(state)
log.info('Results: sent=%d skipped=%d errors=%d', sent, skipped, errors)
print(f'Done: {sent} sent, {skipped} not yet due, {errors} errors')
