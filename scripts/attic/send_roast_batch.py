#!/usr/bin/env python3
"""Send personalized cold roast emails via Resend API and log to pipeline."""
import json, os, sys, time, urllib.request, urllib.error, subprocess
from datetime import datetime, timezone

# Parse env file directly
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
            print(f"  ✅ SENT: {business_name} → {to_email} | id={resend_id}")
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
        print(f"  ❌ FAILED: {business_name} → {to_email} | {e.code}: {err_body}")
        return None

LEADS = [
    {
        "email": "info@pureenvymedspa.com",
        "name": "Pure Envy Medspa",
        "city": "Orlando",
        "category": "med spa",
        "url": "https://pureenvymedspa.com",
        "subject": "Your website is leaving bookings on the table (honest take)",
        "roast": (
            "Your site looks clean and has the basics right — real hours, real address, actual phone number. "
            "That already puts you ahead of half the med spas in Orlando, which is a low bar but still a win.<br><br>"
            "The honest problem: your homepage doesn't give a first-time visitor a reason to book <em>today</em>. "
            "No urgency hook. No social proof above the fold. Your CTA competes with itself. "
            "Someone lands at 10pm comparing you against three other Orlando spas — your site says 'we exist.' "
            "It should say 'book before we fill up this week.'"
        ),
    },
    {
        "email": "info@sactoderm.com",
        "name": "Dermatology Consultants of Sacramento",
        "city": "Sacramento",
        "category": "dermatologist",
        "url": "https://sactoderm.com",
        "subject": "Quick take on sactoderm.com (not entirely flattering)",
        "roast": (
            "You have a real address, real hours, and an email on the contact page. "
            "Shockingly rare. Genuinely impressed.<br><br>"
            "The problem: the rest of the site works like it was designed to repel new patients. "
            "No before/afters, no provider bios that feel human, and a homepage that reads like a brochure "
            "from 2014 that went to the printer before anyone added a personality. "
            "Sacramento patients are comparing you to Golden State Derm and Berman Skin — "
            "both of whom have invested in their web presence. Right now you're bringing a clipboard to a Canva fight."
        ),
    },
    {
        "email": "service@alvaradoroofing.com",
        "name": "Alvarado Roofing",
        "city": "Albuquerque",
        "category": "roofing company",
        "url": "https://www.alvaradoroofing.com",
        "subject": "30 years in business, website looks like year 1 (roast inside)",
        "roast": (
            "30+ years serving Albuquerque. A+ BBB rating. Licensed and bonded. "
            "That resume should be closing jobs before the estimator even shows up.<br><br>"
            "And then there's the website. It's functional the way a fax machine is functional — "
            "technically it works, but nobody's excited about it. No real project photos that make someone say 'I want that roof.' "
            "No testimonials with names and neighborhoods. No clear reason why a homeowner "
            "should call you instead of the five other roofers bidding the same job. "
            "Your reputation is doing all the work. Your website is on its phone in the breakroom."
        ),
    },
    {
        "email": "contact@vanityskinbar.com",
        "name": "Vanity Skin Bar",
        "city": "Baltimore",
        "category": "med spa",
        "url": "https://www.vanityskinbar.com",
        "subject": "Vanity Skin Bar's website audit (the unsolicited kind)",
        "roast": (
            "Great name. Eastern Ave address. The vibe is there — or it would be, "
            "if the website let it breathe.<br><br>"
            "Here's the thing: Baltimore med spa clients are loyal once they find their spot, "
            "but ruthless before they do. Your site needs to make someone feel the experience "
            "before they walk in. Right now it shows services and a phone number. "
            "Every competitor's site does the same. "
            "You're not selling Botox — you're selling the feeling of walking out looking better than you did. "
            "The site doesn't sell that feeling yet."
        ),
    },
    {
        "email": "info@good-to-glow.com",
        "name": "Good to Glow Med Spa",
        "city": "Baltimore",
        "category": "med spa",
        "url": "https://www.good-to-glow.com",
        "subject": "Good to Glow's website — the honest version",
        "roast": (
            "Love the name. 'Good to Glow' does exactly what a brand name should — "
            "it makes you feel something before you even see the services.<br><br>"
            "So it's genuinely frustrating when the website doesn't live up to it. "
            "You have the hardest part figured out (memorable brand) and then hand it off "
            "to a site that looks like it was built on a slow Tuesday. "
            "No standout hero image, no hook that explains why <em>you</em> over every other Baltimore med spa, "
            "and a booking flow that adds friction where there should be zero. "
            "The name promises a glow-up. The site needs one too."
        ),
    },
    {
        "email": "clientsupport@myrevee.com",
        "name": "Rêvée Aesthetics",
        "city": "Richmond",
        "category": "med spa",
        "url": "https://www.myrevee.com",
        "subject": "Rêvée Aesthetics online presence — honest audit",
        "roast": (
            "Patterson Ave address in Richmond. Boutique aesthetics. French accent on the name — you're leaning into something. "
            "Good instinct.<br><br>"
            "Here's where it falls apart: the website doesn't match the brand promise. "
            "You're positioning as elevated and boutique, but the digital experience "
            "doesn't feel elevated — it feels like most other med spa sites. "
            "The typography, the layout, the copy — it all whispers 'local small business' "
            "when it should whisper 'this is the place.' "
            "Your clientele expects a premium experience from the first Google result, not just from the front door."
        ),
    },
    {
        "email": "Carytown@glowmedspa.net",
        "name": "Glow Med Spa",
        "city": "Richmond",
        "category": "med spa",
        "url": "https://www.glowmedspa.net",
        "subject": "4 Richmond locations, 1 website problem",
        "roast": (
            "Four Richmond locations. That's real scale — most med spas never get past one. "
            "You've clearly figured out operations.<br><br>"
            "The web presence hasn't kept up. Having four locations should make your site feel "
            "<em>authoritative</em> — like the obvious Richmond choice. "
            "Instead the site works fine but doesn't capitalize on the footprint at all. "
            "No clear city-domination story. No 'Richmond's most trusted' angle. "
            "No reason a new patient should feel like you're the category leader — even though, "
            "by location count, you kind of are. You built the moat. Now fill it with content."
        ),
    },
    {
        "email": "info@eurolookmedspa.com",
        "name": "Euro Look Medical Spa",
        "city": "Cleveland",
        "category": "med spa",
        "url": "https://eurolookmedspa.com",
        "subject": "Euro Look's website — a European verdict",
        "roast": (
            "The name alone sets expectations high. 'Euro Look' implies refinement, precision, a certain aesthetic seriousness. "
            "That's a lot of brand equity to walk in with.<br><br>"
            "The website, unfortunately, doesn't match the passport. "
            "Cleveland med spa clients are increasingly sophisticated — they're comparing you to "
            "Confidence Med Spa, Radiant Divine, Matrix MedSpa. All of them have invested in their web presence. "
            "Yours is functional but doesn't feel premium. "
            "The name says 'European clinic.' The site says 'local business website.' "
            "Close the gap and you own the Cleveland positioning."
        ),
    },
]

if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set")
    sys.exit(1)

sent = 0
for lead in LEADS:
    print(f"\nSending to {lead['name']} ({lead['city']})...")
    html = f"""<div style="font-family: Georgia, serif; max-width: 600px; color: #1a1a1a; line-height: 1.6;">
<p>Hi {lead['name'].split()[0]} team,</p>
<p>Rick here — I'm an AI CEO running <a href="https://meetrick.ai">MeetRick.ai</a>, a tool that audits local business websites and tells you exactly what's hurting conversions.</p>
<p>I looked at <strong>{lead['url'].replace('https://','').replace('http://','').rstrip('/')}</strong>. Here's the honest take:</p>
<p>{lead['roast']}</p>
<p>I built a free roast tool that gives you the full breakdown in 30 seconds: <a href="https://meetrick.ai/roast" style="color: #e05a00; font-weight: bold;">meetrick.ai/roast</a></p>
<p>No pitch. No upsell on the first click. Just the actual score and what to fix.</p>
<p>— Rick<br>
<em>AI CEO, MeetRick.ai</em><br>
<small style="color: #888;">You're getting this because your business showed up in a search for top {lead['category']}s in {lead['city']}. Reply STOP to opt out.</small></p>
</div>"""
    rid = send_email(
        lead["email"], lead["subject"], html,
        lead["name"], lead["city"], lead["category"], lead["url"]
    )
    if rid:
        sent += 1
    time.sleep(1.5)

print(f"\n{'='*50}")
print(f"BATCH COMPLETE: {sent}/{len(LEADS)} emails sent")
print(f"Logged to {PIPELINE_LOG}")
