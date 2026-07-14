#!/usr/bin/env python3
"""
nurture-dispatch.py — Sends due nurture emails from state.json via Resend API.
Run every 30 minutes via cron. Idempotent and safe to retry.

Usage:
    python3 nurture-dispatch.py              # live send
    python3 nurture-dispatch.py --dry-run    # print what would be sent
"""

import json
import os
import sys
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path

# Allow importing sibling module
sys.path.insert(0, str(Path(__file__).parent))
from nurture_emails_local import EMAIL_FUNCS, EMAIL_DELAYS_HOURS

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error

# --- Config ---
STATE_FILE = os.path.expanduser("~/rick-vault/runtime/nurture/state.json")
LOG_FILE = os.path.expanduser("~/rick-vault/logs/nurture-dispatch.log")
SENT_LOG = os.path.expanduser("~/rick-vault/runtime/nurture/sent.log")
ENV_FILE = os.path.expanduser("~/clawd/config/rick.env")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "Rick <rick@meetrick.ai>"
REPLY_TO = "rick@meetrick.ai"
RESEND_URL = "https://api.resend.com/emails"

# --- Logging ---
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("nurture-dispatch")


def load_env():
    """Load RESEND_API_KEY from rick.env if not in environment."""
    global RESEND_API_KEY
    if RESEND_API_KEY:
        return
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                if line.startswith("RESEND_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    RESEND_API_KEY = val
                    return
    log.error("RESEND_API_KEY not found in env or %s", ENV_FILE)
    sys.exit(1)


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def send_idempotency_key(email, email_num):
    """Generate a stable idempotency key per contact+email combo."""
    return hashlib.sha256(f"nurture-v1:{email}:{email_num}".encode()).hexdigest()[:16]


def load_sent_set():
    """Load set of already-sent idempotency keys."""
    sent = set()
    if os.path.exists(SENT_LOG):
        with open(SENT_LOG) as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 1:
                    sent.add(parts[0])
    return sent


def record_sent(idem_key, email, email_num):
    """Append to sent log for durable idempotency."""
    os.makedirs(os.path.dirname(SENT_LOG), exist_ok=True)
    with open(SENT_LOG, "a") as f:
        f.write(f"{idem_key}\t{email}\t{email_num}\t{datetime.now(timezone.utc).isoformat()}\n")


# Module-level flag: set True when Resend returns daily_quota_exceeded
_QUOTA_EXHAUSTED = False


def send_email(to_email, subject, html_body, dry_run=False):
    """Send a single email via Resend API. Returns True on success."""
    global _QUOTA_EXHAUSTED
    payload = {
        "from": FROM_EMAIL,
        "to": [to_email],
        "reply_to": REPLY_TO,
        "subject": subject,
        "html": html_body,
    }

    if dry_run:
        log.info("[DRY RUN] Would send to %s: %s", to_email, subject)
        return True

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        if HAS_REQUESTS:
            resp = requests.post(RESEND_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code in (200, 201):
                log.info("Sent to %s: %s (id: %s)", to_email, subject, resp.json().get("id", "?"))
                return True
            else:
                log.error("Resend error %d for %s: %s", resp.status_code, to_email, resp.text)
                if resp.status_code == 429:
                    try:
                        body = resp.json()
                        if body.get("name") == "daily_quota_exceeded":
                            _QUOTA_EXHAUSTED = True
                    except Exception:
                        pass
                return False
        else:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(RESEND_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
                log.info("Sent to %s: %s (id: %s)", to_email, subject, body.get("id", "?"))
                return True
    except Exception as e:
        log.error("Failed to send to %s: %s", to_email, e)
        return False


def extract_first_name(email):
    """Best-effort first name from email address."""
    local = email.split("@")[0]
    # Remove common prefixes
    for prefix in ["info", "hello", "contact", "admin", "support", "team", "office", "help"]:
        if local.lower() == prefix:
            return "there"
    # Try to get a name from the local part
    name = local.split(".")[0].split("_")[0].split("-")[0]
    if len(name) < 2 or name.isdigit():
        return "there"
    return name.capitalize()


def process_contacts(state, dry_run=False):
    now = datetime.now(timezone.utc)
    contacts = state.get("contacts", {})
    unsubscribed = set(state.get("unsubscribed", []))
    sent_set = load_sent_set()

    sent_count = 0
    skipped_count = 0
    error_count = 0

    for email, contact in contacts.items():
        if contact.get("status") != "active":
            continue
        if email in unsubscribed:
            contact["status"] = "unsubscribed"
            continue

        emails_sent = contact.get("emails_sent", [])
        next_email_num = len(emails_sent) + 1

        if next_email_num > 5:
            contact["status"] = "completed"
            continue

        # Check if due
        raw_ts = contact["enrolled_at"]
        if raw_ts.endswith("Z"):
            raw_ts = raw_ts[:-1] + "+00:00"
        enrolled_at = datetime.fromisoformat(raw_ts)
        delay_hours = EMAIL_DELAYS_HOURS[next_email_num]
        due_at = enrolled_at.replace(tzinfo=timezone.utc) if enrolled_at.tzinfo is None else enrolled_at
        from datetime import timedelta
        due_at = due_at + timedelta(hours=delay_hours)

        if now < due_at:
            skipped_count += 1
            continue

        # Idempotency check
        idem_key = send_idempotency_key(email, next_email_num)
        if idem_key in sent_set:
            # Already sent but state wasn't updated — fix state
            if next_email_num not in emails_sent:
                emails_sent.append(next_email_num)
                contact["emails_sent"] = emails_sent
            log.info("Already sent email %d to %s (idempotency), fixing state", next_email_num, email)
            continue

        # Generate email
        url = contact.get("url", "your site")
        first_name = contact.get("first_name") or extract_first_name(email)
        email_func = EMAIL_FUNCS[next_email_num]
        subject, html_body = email_func(first_name, url)

        # Send
        success = send_email(email, subject, html_body, dry_run=dry_run)

        if success:
            emails_sent.append(next_email_num)
            contact["emails_sent"] = emails_sent
            contact["last_sent_at"] = now.isoformat()
            contact["next_due_at"] = None
            if next_email_num < 5:
                next_delay = EMAIL_DELAYS_HOURS[next_email_num + 1]
                next_due = enrolled_at + timedelta(hours=next_delay)
                contact["next_due_at"] = next_due.isoformat()
            else:
                contact["status"] = "completed"
            record_sent(idem_key, email, next_email_num)
            sent_count += 1
        else:
            error_count += 1
            # If Resend daily quota is exhausted, abort immediately — no point
            # hammering the remaining contacts and burning pointless API calls.
            if _is_daily_quota_error():
                log.warning("Daily sending quota exhausted — aborting dispatch early. Quota resets at midnight UTC.")
                break

    return sent_count, skipped_count, error_count


def _is_daily_quota_error():
    """Check if the most recent Resend response was a daily quota error."""
    # We inspect the last log entry by keeping a module-level flag set in send_email.
    return _QUOTA_EXHAUSTED


def main():
    dry_run = "--dry-run" in sys.argv
    load_env()

    if not os.path.exists(STATE_FILE):
        log.error("State file not found: %s", STATE_FILE)
        sys.exit(1)

    state = load_state()
    log.info("=== Nurture dispatch run (dry_run=%s) ===", dry_run)
    log.info("Active contacts: %d", sum(1 for c in state.get("contacts", {}).values() if c.get("status") == "active"))

    sent, skipped, errors = process_contacts(state, dry_run=dry_run)

    if not dry_run:
        save_state(state)

    log.info("Results: sent=%d skipped=%d errors=%d", sent, skipped, errors)
    print(f"\nDone: {sent} sent, {skipped} not yet due, {errors} errors")


if __name__ == "__main__":
    main()
