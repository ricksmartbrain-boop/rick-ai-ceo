#!/usr/bin/env python3
"""
local-biz-pipeline.py - Full pipeline: find local businesses, roast websites, send cold emails
Usage: python3 local-biz-pipeline.py "dentist" "Austin TX" --count 5 --send
"""
import json, urllib.request, urllib.parse, re, sys, os, time, subprocess
from datetime import datetime
from email_safety import block_reason_for_recipient

# Bootstrap sys.path so runtime imports work from scripts/
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from runtime.llm import generate_text  # noqa: E402

PIPELINE_FILE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")

# Permanent outreach exclusions — never pitch these domains/emails
EXCLUDED_DOMAINS = [
    "belkins.io",       # Vlad's company (co-founder)
    "meetrick.ai",      # Our own domain
]
EXCLUDED_EMAILS = []

def is_excluded(email_or_url):
    """Return True if this email/URL should be skipped from outreach."""
    target = (email_or_url or "").lower()
    for d in EXCLUDED_DOMAINS:
        if d in target:
            return True
    for e in EXCLUDED_EMAILS:
        if e.lower() in target:
            return True
    return False

def log_pipeline(entry):
    with open(PIPELINE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

def roast_site(url):
    """Roast a website via runtime.llm (route='writing')"""
    # Fetch page content
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")[:50000]
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        html = re.sub(r'<[^>]+>', ' ', html)
        page = re.sub(r'\s+', ' ', html).strip()[:6000]
    except Exception as e:
        return None
    
    domain = re.sub(r'https?://(www\.)?', '', url).rstrip('/')
    
    prompt = f"""Analyze this local business website. Return ONLY valid JSON (no markdown):
{{
  "score": <1-10>,
  "problems": ["problem 1", "problem 2", "problem 3"],
  "wins": ["win 1", "win 2"],
  "verdict": "one line summary",
  "estimated_lost_customers_monthly": "X-Y customers",
  "email_subject": "Your {domain} website is leaving money on the table",
  "email_hook": "personalized opening line referencing something specific on their site"
}}

URL: {url}
Page content: {page}"""

    result = generate_text(route="writing", prompt=prompt, fallback="")
    if result.mode not in ("live", "cached"):
        print(f"  Roast error: LLM call failed (mode={result.mode}, model={result.model})", file=sys.stderr)
        return None
    try:
        # Extract JSON from response
        match = re.search(r'\{.*\}', result.content, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception as e:
        print(f"  Roast error: {e}", file=sys.stderr)
    return None

def send_cold_email(to_email, business_name, roast_data, resend_key):
    """Send personalized cold email with roast results via Resend"""
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        print(f"  Email blocked: {block_reason}", file=sys.stderr)
        return None
    subject = roast_data.get("email_subject", f"Your website could be losing you customers")
    hook = roast_data.get("email_hook", "I took a look at your website and noticed a few things.")
    problems = roast_data.get("problems", [])
    score = roast_data.get("score", "?")
    lost = roast_data.get("estimated_lost_customers_monthly", "unknown")
    
    html = f"""<div style="font-family: -apple-system, sans-serif; max-width: 600px; margin: 0 auto;">
<p>Hi there,</p>
<p>{hook}</p>
<p>I ran an AI analysis of your website and here's what I found:</p>
<p><strong>Score: {score}/10</strong></p>
<p><strong>Top issues costing you customers:</strong></p>
<ol>
{"".join(f"<li>{p}</li>" for p in problems[:3])}
</ol>
<p>Estimated impact: <strong>{lost} potential customers/month</strong> may be bouncing.</p>
<p>I put together a detailed report with specific fixes. Want me to send it over?</p>
<p>Or if you'd prefer, I can walk you through it in a quick 30-minute call: <a href="https://meetrick.ai/roast">Get your full free report</a></p>
<p>Best,<br>Rick<br>AI CEO, MeetRick.ai<br><em>I analyze websites so you can focus on your patients/clients</em></p>
<p style="font-size:11px;color:#999;">Not interested? Just reply "stop" and I won't email again.</p>
</div>"""

    payload = json.dumps({
        "from": "Rick <rick@meetrick.ai>",
        "to": [to_email],
        "subject": subject,
        "html": html
    }).encode()
    
    # Use curl for Resend (urllib has SSL/redirect issues with Resend API)
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
             "-H", f"Authorization: Bearer {resend_key}",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"from": "Rick <rick@meetrick.ai>", "to": [to_email], "subject": subject, "html": html})],
            capture_output=True, text=True, timeout=15
        )
        resp = json.loads(result.stdout)
        if resp.get("id"):
            return resp["id"]
        else:
            print(f"  Email error: {resp}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"  Email error: {e}", file=sys.stderr)
        return None

def extract_email_from_site(url):
    """Try to find a contact email on the website"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")[:100000]
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', html)
        # Filter out common non-contact emails
        filtered = [e for e in emails if not any(x in e.lower() for x in ['example.com', 'sentry.io', 'schema.org', 'w3.org', 'wixpress', 'placeholder'])]
        return filtered[0] if filtered else None
    except:
        return None

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("business_type", help="e.g., dentist, salon, realtor")
    parser.add_argument("location", help="e.g., Austin TX")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--send", action="store_true", help="Actually send emails")
    args = parser.parse_args()
    
    resend_key = os.environ.get("RESEND_API_KEY", "")
    
    print(f"=== Local Biz Pipeline: {args.business_type} in {args.location} ===")
    print(f"Mode: {'LIVE SEND' if args.send else 'DRY RUN'}")
    
    # For now, use hardcoded results from web search (will be replaced with Google Places API)
    # This is the manual kickstart - automation comes next
    print(f"\nTo run: use web_search results and pipe URLs here")
    print("Usage with URLs: echo 'https://site1.com\\nhttps://site2.com' | python3 local-biz-pipeline.py dentist 'Austin TX' --count 5")
    
    # Read URLs from stdin if piped
    import select
    if select.select([sys.stdin], [], [], 0.0)[0]:
        urls = [line.strip() for line in sys.stdin if line.strip().startswith("http")]
    else:
        urls = []
    
    for url in urls[:args.count]:
        print(f"\n--- Processing: {url} ---")
        
        # Skip excluded domains
        if is_excluded(url):
            print(f"  SKIPPED (excluded domain)")
            continue

        # Extract email
        email = extract_email_from_site(url)
        print(f"  Email found: {email or 'none'}")

        # Skip excluded emails
        if email and is_excluded(email):
            print(f"  SKIPPED (excluded email: {email})")
            continue
        
        # Roast
        print("  Roasting...")
        roast = roast_site(url)
        if roast:
            print(f"  Score: {roast.get('score', '?')}/10")
            print(f"  Problems: {roast.get('problems', [])[:2]}")
            
            # Log to pipeline
            log_pipeline({
                "ts": datetime.utcnow().isoformat() + "Z",
                "stage": "roasted",
                "source": "google_maps",
                "target": url,
                "email": email,
                "score": roast.get("score"),
                "category": args.business_type,
                "location": args.location,
                "channel": "cold_email"
            })
            
            # Send email if we have one and --send flag
            if email and args.send and resend_key:
                print(f"  Sending email to {email}...")
                eid = send_cold_email(email, "", roast, resend_key)
                if eid:
                    print(f"  Sent! ID: {eid}")
                    log_pipeline({
                        "ts": datetime.utcnow().isoformat() + "Z",
                        "stage": "contacted",
                        "source": "google_maps",
                        "target": url,
                        "email": email,
                        "email_id": eid,
                        "channel": "cold_email"
                    })
        else:
            print("  Roast failed")
    
    print(f"\nDone. Pipeline: {PIPELINE_FILE}")
