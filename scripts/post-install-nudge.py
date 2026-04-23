#!/usr/bin/env python3
"""
Rick Post-Install Day-2 Nudge

TIER-3 #4 of the swarm-of-experts revenue plan: convert ~10% of fresh free-tier
installs to Pro by sending a single human-voice follow-up 48-72h after install.

How it works
------------
1. Pull /api/v1/stats from meetrick-api -> recent_installs.
2. Filter to candidates that are:
     - tier == 'free'
     - joined_at between 48h and 72h ago (UTC)
     - callsign NOT yet in the idempotency log
     - tier of operator NOT 'pro' or 'business' (double-checked)
     - email is known locally AND not in suppression.txt
3. Render the markdown template at ~/rick-vault/email-sequences/post-install/day2.md.
4. POST to Resend (https://api.resend.com/emails) — DRY-RUN by default.
5. Log every attempt + result to ~/rick-vault/operations/post-install-nudges.jsonl.
6. Append to ~/rick-vault/data/post-install-nudge-sent.jsonl on success so we
   never email the same callsign twice.

Email lookup
------------
The /stats endpoint does NOT expose operator email addresses. The script reads
~/rick-vault/data/callsign-emails.json (a hand-maintained map of callsign ->
email) and skips any candidate whose email is unknown. Vlad populates that file
as he learns operator emails (Stripe metadata, support contact, signup forms,
etc.). Skipping is logged with reason="no_email_known" so the gap is visible.

Safety
------
- DRY-RUN unless RICK_POST_INSTALL_NUDGE_LIVE=1 in env.
- Hard ceiling: MAX_SENDS_PER_DAY = 20.
- Suppression list at ~/rick-vault/mailbox/suppression.txt is honoured.
- Paying tiers (pro, business) are explicitly excluded.
- Resend errors (incl. 429) are logged + skipped, never raised.
- Never crashes — every failure logged and accounted for.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- Paths & config ---
HOME = Path.home()
ENV_FILE = HOME / "clawd" / "config" / "rick.env"
TEMPLATE_FILE = HOME / "rick-vault" / "email-sequences" / "post-install" / "day2.md"
SENT_LOG = HOME / "rick-vault" / "data" / "post-install-nudge-sent.jsonl"
OP_LOG = HOME / "rick-vault" / "operations" / "post-install-nudges.jsonl"
EMAIL_MAP_FILE = HOME / "rick-vault" / "data" / "callsign-emails.json"
SUPPRESSION_FILE = HOME / "rick-vault" / "mailbox" / "suppression.txt"

STATS_URL = "https://api.meetrick.ai/api/v1/stats"
RESEND_URL = "https://api.resend.com/emails"
FROM_ADDRESS = "Rick <hello@meetrick.ai>"
REPLY_TO = "vladislav@belkins.io"

PRICING_URL = "https://meetrick.ai/pricing?utm_source=post-install-nudge&utm_campaign=day2"
CALENDLY_URL = "https://cal.com/vladislav-belkins/30min"
FROM_NAME = "Vlad"

WINDOW_MIN_HOURS = 48
WINDOW_MAX_HOURS = 72
MAX_SENDS_PER_DAY = 20
LIVE_FLAG = "RICK_POST_INSTALL_NUDGE_LIVE"


# --- Env loading ---
def load_resend_key():
    """Read RESEND_API_KEY from env or rick.env file."""
    key = os.environ.get("RESEND_API_KEY")
    if key:
        return key
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, _, v = line.partition("=")
        if k.strip() == "RESEND_API_KEY":
            return v.strip().strip('"').strip("'")
    return None


def is_live():
    """Check if the live-send flag is set in env or rick.env."""
    if os.environ.get(LIVE_FLAG) == "1":
        return True
    if not ENV_FILE.exists():
        return False
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == LIVE_FLAG and v.strip().strip('"').strip("'") == "1":
                return True
    return False


# --- Data loading ---
def fetch_stats():
    req = urllib.request.Request(STATS_URL, headers={"User-Agent": "rick-post-install-nudge/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        log_op({"event": "stats_fetch_failed", "error": str(e)})
        return None


def load_email_map():
    if not EMAIL_MAP_FILE.exists():
        EMAIL_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        EMAIL_MAP_FILE.write_text("{}\n")
        return {}
    try:
        return json.loads(EMAIL_MAP_FILE.read_text() or "{}")
    except Exception as e:
        log_op({"event": "email_map_load_failed", "error": str(e)})
        return {}


def load_suppression():
    if not SUPPRESSION_FILE.exists():
        return set()
    return {
        line.strip().lower()
        for line in SUPPRESSION_FILE.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


def load_sent_callsigns():
    """Set of callsigns already nudged (idempotency)."""
    sent = set()
    if not SENT_LOG.exists():
        return sent
    for line in SENT_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            cs = row.get("callsign")
            if cs:
                sent.add(cs.lower())
        except json.JSONDecodeError:
            continue
    return sent


def sends_today_count():
    """How many SUCCESSFUL sends we've already made in the current UTC day."""
    if not OP_LOG.exists():
        return 0
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    n = 0
    for line in OP_LOG.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") == "send_success" and (row.get("ts", "")[:10] == today):
            n += 1
    return n


# --- Logging ---
def log_op(payload):
    OP_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload.setdefault("ts", dt.datetime.utcnow().isoformat() + "Z")
    with open(OP_LOG, "a") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def log_sent(callsign, email, mode):
    SENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SENT_LOG, "a") as f:
        f.write(json.dumps({
            "ts": dt.datetime.utcnow().isoformat() + "Z",
            "callsign": callsign,
            "email": email,
            "mode": mode,
        }) + "\n")


# --- Template rendering ---
def parse_template(text):
    """Pull subject, html body, and plain-text body from day2.md."""
    subject_m = re.search(r"^##\s*Subject\s*\n([^\n]+)", text, re.MULTILINE)
    subject = subject_m.group(1).strip() if subject_m else "Quick note about your Rick"
    html_m = re.search(r"^##\s*Body \(HTML\)\s*\n(.+?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    html = html_m.group(1).strip() if html_m else ""
    text_m = re.search(r"^##\s*Plain-text fallback\s*\n(.+?)(?=^##\s|\Z)", text, re.MULTILINE | re.DOTALL)
    plain = text_m.group(1).strip() if text_m else ""
    return subject, html, plain


def render(template_str, vars_):
    out = template_str
    for k, v in vars_.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def humanise_age(hours):
    if hours < 36:
        return "about a day ago"
    if hours < 60:
        return "two days ago"
    return "three days ago"


# --- Resend ---
def send_via_resend(api_key, to_email, subject, html, plain):
    payload = {
        "from": FROM_ADDRESS,
        "to": [to_email],
        "subject": subject,
        "html": html,
        "text": plain,
        "reply_to": REPLY_TO,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
            return True, body.get("id"), None
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode()
        except Exception:
            err_body = ""
        return False, None, f"HTTP {e.code}: {err_body[:300]}"
    except Exception as e:
        return False, None, f"{type(e).__name__}: {str(e)[:300]}"


# --- Main flow ---
def find_candidates(stats, sent_callsigns):
    """Return list of dicts: install rows that fall in the 48-72h window
    and aren't paying / aren't already nudged."""
    if not stats:
        return []
    installs = stats.get("recent_installs") or stats.get("top_recent_callsigns") or []
    now = dt.datetime.utcnow()
    out = []
    for row in installs:
        callsign = row.get("callsign")
        tier = (row.get("tier") or "").lower()
        ts = row.get("joined_at") or row.get("timestamp")
        if not callsign or tier in ("pro", "business") or not ts:
            continue
        if tier != "free":
            continue
        if callsign.lower() in sent_callsigns:
            continue
        try:
            joined = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            continue
        age_h = (now - joined).total_seconds() / 3600
        if WINDOW_MIN_HOURS <= age_h <= WINDOW_MAX_HOURS:
            out.append({
                "callsign": callsign,
                "country": row.get("country") or "",
                "joined_at": ts,
                "age_hours": round(age_h, 1),
            })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run regardless of env flag")
    parser.add_argument("--show-template", action="store_true", help="Print rendered email for first candidate and exit")
    args = parser.parse_args()

    started = dt.datetime.utcnow()
    print(f"=== post-install-nudge {started.isoformat()}Z ===")

    if not TEMPLATE_FILE.exists():
        print(f"ERROR: template not found at {TEMPLATE_FILE}")
        log_op({"event": "template_missing", "path": str(TEMPLATE_FILE)})
        sys.exit(1)

    template_text = TEMPLATE_FILE.read_text()
    subject_tmpl, html_tmpl, plain_tmpl = parse_template(template_text)
    if not html_tmpl:
        print("ERROR: empty HTML body in template")
        log_op({"event": "template_empty"})
        sys.exit(1)

    live_env = is_live()
    dry_run = args.dry_run or not live_env
    mode = "dry-run" if dry_run else "LIVE"
    print(f"mode={mode}  (env {LIVE_FLAG}={'1' if live_env else 'unset'}, --dry-run={args.dry_run})")

    stats = fetch_stats()
    if stats is None:
        print("ERROR: could not fetch /stats; aborting cleanly")
        sys.exit(0)

    sent_callsigns = load_sent_callsigns()
    suppression = load_suppression()
    email_map = load_email_map()
    candidates = find_candidates(stats, sent_callsigns)
    print(f"candidates_in_window={len(candidates)}  already_sent={len(sent_callsigns)}  email_map_size={len(email_map)}")

    if not candidates:
        log_op({"event": "run_complete", "mode": mode, "candidates": 0, "sent": 0})
        print("Nothing to do.")
        return

    api_key = load_resend_key()
    if not api_key and not dry_run:
        print("ERROR: no RESEND_API_KEY but live-send requested; refusing")
        log_op({"event": "abort_no_key"})
        sys.exit(1)

    sends_already = sends_today_count()
    remaining = max(0, MAX_SENDS_PER_DAY - sends_already)
    print(f"sends_today_already={sends_already}  remaining_quota={remaining}")

    sent = 0
    skipped = 0
    for cand in candidates:
        callsign = cand["callsign"]
        email = email_map.get(callsign) or email_map.get(callsign.lower())
        if not email:
            skipped += 1
            log_op({"event": "skip", "reason": "no_email_known", "callsign": callsign})
            print(f"  - skip {callsign}: no email in callsign-emails.json")
            continue
        if email.lower() in suppression:
            skipped += 1
            log_op({"event": "skip", "reason": "suppressed", "callsign": callsign, "email": email})
            print(f"  - skip {callsign} <{email}>: suppression list")
            continue
        if not dry_run and sent >= remaining:
            log_op({"event": "skip", "reason": "daily_cap", "callsign": callsign})
            print(f"  - skip {callsign}: daily cap of {MAX_SENDS_PER_DAY} reached")
            skipped += 1
            continue

        vars_ = {
            "callsign": callsign,
            "country": cand["country"] or "your region",
            "joined_human": humanise_age(cand["age_hours"]),
            "pricing_url": PRICING_URL,
            "calendly_url": CALENDLY_URL,
            "from_name": FROM_NAME,
        }
        subject = render(subject_tmpl, vars_)
        html = render(html_tmpl, vars_)
        plain = render(plain_tmpl, vars_)

        if args.show_template:
            print("\n--- RENDERED EMAIL ---")
            print(f"To: {email}\nSubject: {subject}\n\n{plain}\n----------------------\n")
            return

        if dry_run:
            sent += 1
            log_op({"event": "dry_run_would_send", "callsign": callsign, "email": email,
                    "subject": subject})
            print(f"  + DRY {callsign} <{email}> :: {subject}")
            continue

        ok, msg_id, err = send_via_resend(api_key, email, subject, html, plain)
        if ok:
            sent += 1
            log_op({"event": "send_success", "callsign": callsign, "email": email,
                    "resend_id": msg_id, "subject": subject})
            log_sent(callsign, email, mode="live")
            print(f"  + SENT {callsign} <{email}> id={msg_id}")
        else:
            skipped += 1
            log_op({"event": "send_failed", "callsign": callsign, "email": email,
                    "error": err})
            print(f"  ! FAIL {callsign} <{email}>: {err}")
        time.sleep(0.5)  # be polite to Resend

    log_op({"event": "run_complete", "mode": mode, "candidates": len(candidates),
            "sent": sent, "skipped": skipped})
    print(f"done. mode={mode} sent={sent} skipped={skipped}")

    # Wave-6 — alert Vlad when free installs are skipped due to missing emails.
    # Without this the post-install nudge silently leaks every fresh free user.
    _emap = load_email_map()
    no_email_skips = [c for c in candidates if not _emap.get(c.get("callsign", ""))]
    if len(no_email_skips) >= 2:
        try:
            import subprocess  # noqa: WPS433
            tg_script = HOME / "clawd" / "scripts" / "tg-topic.sh"
            if tg_script.is_file():
                names = ", ".join(c.get("callsign", "?") for c in no_email_skips[:8])
                msg = (
                    f"📭 Post-install nudge: {len(no_email_skips)} free installs in 48-72h "
                    f"window have NO email known.\n\n"
                    f"Callsigns: {names}\n\n"
                    f"Fix: add to ~/rick-vault/data/callsign-emails.json — "
                    f"`{{\"Mochi\": \"founder@example.com\", ...}}` — "
                    f"then next 06:00 PT cron will email them the day-2 nudge."
                )
                subprocess.run(
                    ["bash", str(tg_script), "customer", msg],
                    capture_output=True, text=True, timeout=15, check=False,
                )
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
    except Exception as e:
        log_op({"event": "fatal", "error": f"{type(e).__name__}: {e}"})
        print(f"FATAL: {type(e).__name__}: {e}")
        sys.exit(1)
