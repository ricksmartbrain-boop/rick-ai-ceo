#!/usr/bin/env python3
"""
Rick's 5-Day Email Drip Sender

Reads subscribers from ~/rick-vault/projects/email-drip/subscribers.json,
calculates which day email each subscriber should receive based on their
signup_date, sends the appropriate email via Resend transactional API,
and logs all sends.

Designed to run hourly via cron.
"""

import json
import os
import sys
import datetime
import urllib.request
import urllib.error
from pathlib import Path

# --- Config ---
SUBSCRIBERS_FILE = os.path.expanduser("~/rick-vault/projects/email-drip/subscribers.json")
COURSE_DIR = os.path.expanduser("~/rick-vault/projects/email-drip/5-day-course")
LOG_FILE = os.path.expanduser("~/rick-vault/projects/email-drip/drip-log.md")
ENV_FILE = os.path.expanduser("~/.openclaw/workspace/config/rick.env")
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SUPPRESSION_FILE = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "mailbox" / "suppression.txt"

RESEND_API_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "Rick <rick@meetrick.ai>"

# Day config: day_number -> (subject, html_file)
DAY_CONFIG = {
    1: ("Day 1: Why Most AI Agents Forget Everything", "day-1.html"),
    2: ("Day 2: How to Give Your Agent a Real Personality", "day-2.html"),
    3: ("Day 3: The Operating Rhythm That Keeps Your Agent Shipping", "day-3.html"),
    4: ("Day 4: Giving Your Agent Real Power", "day-4.html"),
    5: ("Day 5: Putting It All Together (+ The Full Blueprint)", "day-5.html"),
}

DRY_RUN_EMAILS = {"rick@meetrick.ai"}  # Never actually send to these


def email_channel_block_reason():
    """Return a block reason when the shared email kill switch is not open."""
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.db import connect
        from runtime.kill_switches import ChannelPaused, assert_channel_active

        conn = connect()
        try:
            assert_channel_active(conn, "email")
            return None
        except ChannelPaused as exc:
            return exc.reason
        finally:
            conn.close()
    except Exception as exc:
        return f"gate_unavailable: {type(exc).__name__}: {exc}"


def load_suppressions():
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        lines = SUPPRESSION_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    result = set()
    for raw in lines:
        email = raw.split("#", 1)[0].strip().lower()
        if email:
            result.add(email)
    return result


def load_env():
    """Load RESEND_API_KEY from rick.env if not in environment."""
    api_key = os.environ.get("RESEND_API_KEY")
    if api_key:
        return api_key

    env_path = Path(ENV_FILE)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            # Handle export VAR=val and VAR=val
            if line.startswith("export "):
                line = line[7:]
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "RESEND_API_KEY":
                return val

    return None


def load_subscribers():
    """Load subscriber list, creating empty file if needed."""
    path = Path(SUBSCRIBERS_FILE)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]")
        return []

    with open(path) as f:
        return json.load(f)


def save_subscribers(subscribers):
    """Save subscriber list back to disk."""
    with open(SUBSCRIBERS_FILE, "w") as f:
        json.dump(subscribers, f, indent=2, default=str)


def load_email_html(day_num):
    """Load the HTML content for a given day."""
    _, filename = DAY_CONFIG[day_num]
    filepath = os.path.join(COURSE_DIR, filename)
    with open(filepath) as f:
        return f.read()


def recipient_gate(to_email):
    """Unified fail-closed per-recipient gate (kill_switches.is_send_allowed)."""
    try:
        root = str(WORKSPACE_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from runtime.kill_switches import is_send_allowed

        return is_send_allowed(to_email, cold=False)
    except Exception as exc:
        return False, f"gate_unavailable: {type(exc).__name__}: {exc}"


def send_email(api_key, to_email, to_name, subject, html_content, dry_run=False):
    """Send a single email via Resend API. Returns True on success."""
    allowed, gate_reason = recipient_gate(to_email)
    if not allowed:
        print(f"  SEND_BLOCKED reason={gate_reason} to={to_email}")
        return False
    if dry_run:
        print(f"  [DRY RUN] Would send '{subject}' to {to_email}")
        return True

    payload = {
        "from": FROM_ADDRESS,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }

    if to_name:
        payload["to"] = [f"{to_name} <{to_email}>"]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "meetrick-rick/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            print(f"  ✅ Sent '{subject}' to {to_email} — id: {result.get('id', 'unknown')}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        print(f"  ❌ Failed to send to {to_email}: {e.code} {body}")
        return False
    except Exception as e:
        print(f"  ❌ Failed to send to {to_email}: {e}")
        return False


def log_send(to_email, day_num, dry_run=False):
    """Append a line to the drip log."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    prefix = "[DRY RUN] " if dry_run else ""
    line = f"- {prefix}{now} — Day {day_num} → {to_email}\n"

    log_path = Path(LOG_FILE)
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("# Drip Send Log\n\n")

    with open(log_path, "a") as f:
        f.write(line)


def process_subscribers(api_key):
    """Main processing loop: check each subscriber and send appropriate emails."""
    subscribers = load_subscribers()
    if not subscribers:
        print("No subscribers found.")
        return

    now = datetime.datetime.utcnow()
    sends = 0
    skips = 0
    suppressions = load_suppressions()

    for sub in subscribers:
        email = sub.get("email", "")
        name = sub.get("name", "")
        signup_str = sub.get("signup_date", "")
        day_sent = sub.get("day_sent", 0)

        if not email or not signup_str:
            print(f"  ⚠️  Skipping incomplete subscriber: {sub}")
            continue
        if email.strip().lower() in suppressions:
            print(f"  SUPPRESSED: skipping {email}")
            skips += 1
            continue

        # Parse signup date
        try:
            signup_date = datetime.datetime.strptime(signup_str, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            try:
                signup_date = datetime.datetime.strptime(signup_str, "%Y-%m-%d")
            except ValueError:
                print(f"  ⚠️  Bad date for {email}: {signup_str}")
                continue

        # Calculate which day they should be on
        hours_since_signup = (now - signup_date).total_seconds() / 3600
        eligible_day = 1  # Day 1 is immediate
        if hours_since_signup >= 96:
            eligible_day = 5
        elif hours_since_signup >= 72:
            eligible_day = 4
        elif hours_since_signup >= 48:
            eligible_day = 3
        elif hours_since_signup >= 24:
            eligible_day = 2

        # Already sent this day or beyond?
        next_day_to_send = day_sent + 1
        if next_day_to_send > eligible_day or next_day_to_send > 5:
            skips += 1
            continue

        # Send the next email they're due
        is_dry_run = email.lower() in DRY_RUN_EMAILS
        subject, _ = DAY_CONFIG[next_day_to_send]
        html = load_email_html(next_day_to_send)

        success = send_email(api_key, email, name, subject, html, dry_run=is_dry_run)

        if success:
            sub["day_sent"] = next_day_to_send
            sub["last_sent_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
            log_send(email, next_day_to_send, dry_run=is_dry_run)
            sends += 1

    # Save updated state
    save_subscribers(subscribers)
    print(f"\nDone. Sent: {sends}, Skipped (up-to-date): {skips}, Total: {len(subscribers)}")


def main():
    print(f"=== Rick Drip Sender — {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} ===")

    api_key = load_env()
    if not api_key:
        print("❌ RESEND_API_KEY not found in env or config/rick.env")
        sys.exit(1)

    print(f"📧 Resend API key: ...{api_key[-6:]}")
    print(f"📂 Subscribers: {SUBSCRIBERS_FILE}")
    print(f"📂 Course dir: {COURSE_DIR}")
    print()

    block_reason = email_channel_block_reason()
    if block_reason:
        print(f"EMAIL CHANNEL PAUSED: {block_reason}", file=sys.stderr)
        sys.exit(0)

    process_subscribers(api_key)


if __name__ == "__main__":
    main()
