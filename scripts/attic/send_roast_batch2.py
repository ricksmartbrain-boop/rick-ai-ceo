#!/usr/bin/env python3
"""Send the final 2 personalized cold roast emails."""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

def _load_env(path):
    env = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if line.startswith('export '):
                    line = line[7:]
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env

_env = _load_env("~/.openclaw/workspace/config/rick.env")
RESEND_API_KEY = _env.get("RESEND_API_KEY") or os.environ.get("RESEND_API_KEY", "")
PIPELINE_LOG = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPPRESSION_FILE = os.path.join(os.environ.get("RICK_DATA_ROOT", os.path.expanduser("~/rick-vault")), "mailbox", "suppression.txt")

def email_channel_block_reason():
    try:
        if WORKSPACE_ROOT not in sys.path:
            sys.path.insert(0, WORKSPACE_ROOT)
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

def is_suppressed(email):
    target = (email or "").strip().lower()
    if not target:
        return True
    if not os.path.exists(SUPPRESSION_FILE):
        return False
    try:
        with open(SUPPRESSION_FILE, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                suppressed = raw.split("#", 1)[0].strip().lower()
                if suppressed and suppressed == target:
                    return True
    except OSError:
        return True
    return False

def send_email(to_email, subject, html_body, business_name, city, category, url):
    block_reason = email_channel_block_reason()
    if block_reason:
        print(f"  BLOCKED: email channel paused: {block_reason}")
        return None
    if is_suppressed(to_email):
        print(f"  BLOCKED: suppressed recipient {to_email}")
        return None
    payload = {
        "from": "Rick <rick@meetrick.ai>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read())
            resend_id = result.get("id", "")
            print(f"  SENT: {business_name} -> {to_email} | id={resend_id}")
            log_entry = {
                "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "stage": "contacted",
                "email": to_email,
                "business_name": business_name,
                "city": city,
                "category": category,
                "url": url,
                "resend_id": resend_id,
            }
            with open(PIPELINE_LOG, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
            return resend_id
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        print(f"  FAILED: {business_name} -> {to_email} | {e.code}: {err_body}")
        return None

LEADS = [
    {
        "email": "info@premierpts.com",
        "name": "Premier Physical Therapy Services",
        "city": "Cincinnati",
        "category": "physical therapist",
        "url": "https://www.premierphysicaltherapyservices.com",
        "subject": "Your PT clinic's website — an unsolicited audit",
        "roast": (
            "Physical therapy is a referral-heavy business — most patients come from surgeons and primary care docs. "
            "Which makes your website feel like a formality rather than a revenue engine. Totally understandable.<br><br>"
            "Here's the thing though: self-referrals are growing. People Google 'physical therapy Cincinnati' after a sports injury, "
            "a post-surgical recovery, or a chronic pain flare at 9pm. Right now your site doesn't win that search, "
            "and even if it did, it doesn't convert it. No patient success stories. No clear specialties that make someone "
            "say 'that's exactly my problem.' No urgency for same-week availability (if you have it). "
            "You're leaving self-referrals on the table every week."
        ),
    },
    {
        "email": "info@confluencefp.com",
        "name": "Confluence Financial Partners",
        "city": "Pittsburgh",
        "category": "financial advisor",
        "url": "https://www.confluencefp.com",
        "subject": "Confluence FP's website — the client acquisition angle",
        "roast": (
            "Four offices. Certified Financial Planners. 'Confluence' is a genuinely good name for a wealth management firm — "
            "it implies convergence, strategy, rivers meeting. Strong instinct.<br><br>"
            "The website, though, does the thing every advisory firm website does: it talks about what you do, "
            "not who you do it for. 'We help clients achieve their financial goals' is true of every advisor in Pittsburgh. "
            "The question high-net-worth prospects are actually asking is: 'Are you the right fit for someone like me?' "
            "The site never answers that. No specific client profiles, no real point of view on wealth management philosophy, "
            "no reason to choose you over Fragasso or Mariner down the road. "
            "The credibility is there. The differentiation isn't."
        ),
    },
]

if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set")
    exit(1)

sent = 0
for lead in LEADS:
    print(f"\nSending to {lead['name']} ({lead['city']})...")
    html = f"""<div style="font-family: Georgia, serif; max-width: 600px; color: #1a1a1a; line-height: 1.6;">
<p>Hi {lead['name'].split()[0]} team,</p>
<p>Rick here — I run <a href="https://meetrick.ai">MeetRick.ai</a>, an AI tool that audits local business websites and tells you exactly what's costing you clients.</p>
<p>I ran your site through it. Here's the honest take on <strong>{lead['url'].replace('https://','').replace('http://','').rstrip('/')}</strong>:</p>
<p>{lead['roast']}</p>
<p>I built a free public version anyone can use: <a href="https://meetrick.ai/roast" style="color: #e05a00; font-weight: bold;">meetrick.ai/roast</a></p>
<p>30 seconds. Full score. No pitch on the first click.</p>
<p>— Rick<br>
<em>AI CEO, MeetRick.ai</em><br>
<small style="color: #888;">Your business came up in a search for top {lead['category']}s in {lead['city']}. Reply STOP to opt out.</small></p>
</div>"""
    rid = send_email(
        lead["email"], lead["subject"], html,
        lead["name"], lead["city"], lead["category"], lead["url"]
    )
    if rid:
        sent += 1
    time.sleep(1.5)

print(f"\n{'='*50}")
print(f"BATCH 2 COMPLETE: {sent}/{len(LEADS)} emails sent")
