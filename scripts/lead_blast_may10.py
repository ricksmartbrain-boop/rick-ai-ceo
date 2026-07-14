#!/usr/bin/env python3
"""
Lead Scrape + Blast — May 10, 2026
Uses Bing via agent-browser to find local businesses, extracts emails, sends cold roast emails.
"""

import json, os, re, subprocess, sys, time, tempfile, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone
from email_safety import block_reason_for_recipient

# ── Config ────────────────────────────────────────────────────────────────────
PIPELINE_FILE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DISPLAY = "Rick <rick@meetrick.ai>"
ROAST_URL = "https://meetrick.ai/roast"
TARGET_COUNT = 14

SEARCHES = [
    ("best dermatologist in Tampa FL website", "Tampa", "FL", "dermatologist"),
    ("roofing company Tampa FL contact website", "Tampa", "FL", "roofing"),
    ("best med spa in Memphis TN website", "Memphis", "TN", "med_spa"),
    ("roofing contractor Memphis TN website", "Memphis", "TN", "roofing"),
    ("dermatologist Albuquerque NM website contact", "Albuquerque", "NM", "dermatologist"),
    ("med spa Louisville KY website", "Louisville", "KY", "med_spa"),
    ("financial advisor Detroit MI small business website", "Detroit", "MI", "financial_advisor"),
    ("dermatologist Cincinnati OH website", "Cincinnati", "OH", "dermatologist"),
    ("physical therapist Sacramento CA website contact", "Sacramento", "CA", "physical_therapist"),
    ("financial advisor Baltimore MD website", "Baltimore", "MD", "financial_advisor"),
    ("dermatologist Columbus OH website contact", "Columbus", "OH", "dermatologist"),
    ("med spa Cleveland OH website", "Cleveland", "OH", "med_spa"),
    ("roofing company Albuquerque NM website", "Albuquerque", "NM", "roofing"),
    ("physical therapist Louisville KY website", "Louisville", "KY", "physical_therapist"),
]

# ── Load existing pipeline domains ────────────────────────────────────────────
def load_existing_domains():
    domains = set()
    if not os.path.exists(PIPELINE_FILE):
        return domains
    with open(PIPELINE_FILE) as f:
        for line in f:
            try:
                r = json.loads(line.strip())
                if r.get("domain"):
                    domains.add(r["domain"].lower().strip())
                if r.get("email") and "@" in r.get("email", ""):
                    domains.add(r["email"].split("@")[1].lower().strip())
            except Exception:
                pass
    return domains

# ── agent-browser wrapper ─────────────────────────────────────────────────────
def ab(*args, timeout=20):
    try:
        result = subprocess.run(
            ["agent-browser"] + list(args),
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return ""

SKIP_DOMAINS = {
    'yelp.', 'facebook.', 'instagram.', 'healthgrades.', 'zocdoc.',
    'vitals.', 'webmd.', 'wikipedia.', 'youtube.', 'twitter.', 'linkedin.',
    'angi.', 'thumbtack.', 'ratemds.', 'doximity.', 'google.', 'schema.',
    'w3.org', 'gstatic.', 'googleapis.', 'tebra.com', 'practicefusion.',
    'drchrono.', 'athena', 'kareo.', 'webflow.', 'wix.com', 'squarespace.',
    'shopify.', 'godaddy.', 'wordpress.', 'bing.', 'microsoft.', 'msn.',
    'realself.', 'psychology', 'healthline.', 'mayoclinic.', 'webmd.',
    'npi', 'npidb', 'npino', 'medicaldirector', 'doctorspring.',
}

def search_bing(query, max_results=5):
    """Search Bing via agent-browser, return list of (domain, url) tuples."""
    encoded = urllib.parse.quote(query)
    url = f"https://www.bing.com/search?q={encoded}"
    
    print(f"  🔍 Bing: {query[:60]}")
    
    ab("open", url, timeout=15)
    time.sleep(2)
    ab("wait", "--load", "networkidle", timeout=10)
    
    snap = ab("snapshot", "--json", timeout=15)
    
    try:
        text = json.dumps(json.loads(snap))
    except Exception:
        text = snap
    
    url_pattern = r'https?://(?!(?:www\.bing\.|bing\.|microsoft\.|go\.microsoft\.|msn\.))([a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+)(?:/[^\s"<>]*)?'
    matches = re.findall(url_pattern, text)
    
    seen = set()
    results = []
    
    # Also try to find full URLs
    full_urls = re.findall(r'https?://[a-zA-Z0-9\-\.]+/[^\s"<>]*', text)
    
    for domain in matches:
        domain_lower = domain.lower()
        if any(s in domain_lower for s in SKIP_DOMAINS):
            continue
        if domain_lower in seen:
            continue
        if '.' not in domain_lower:
            continue
        # Must look like a real domain (not an IP, not too long)
        parts = domain_lower.split('.')
        if len(parts) < 2 or len(parts[-1]) < 2:
            continue
        
        seen.add(domain_lower)
        results.append((domain_lower, f"https://www.{domain_lower}"))
        if len(results) >= max_results:
            break
    
    return results

# ── Email extraction ──────────────────────────────────────────────────────────
def fetch_url(url, timeout=8):
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml',
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return ""

def extract_email(base_url):
    skip_patterns = [
        'sentry', 'wixpress', 'schema', 'example', 'placeholder',
        'noreply', 'no-reply', 'donotreply', 'wordpress', 'gravatar',
        'jquery', '.png', '.jpg', '.gif', 'support@wix', 'support@wordpress',
    ]
    
    for path in ['', '/contact', '/contact-us', '/about', '/about-us']:
        html = fetch_url(base_url.rstrip('/') + path)
        if not html:
            continue
        
        # Decode HTML entities for mailto links
        html_decoded = html.replace('%40', '@').replace('&#64;', '@').replace('&amp;', '&')
        
        emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html_decoded)
        
        for email in emails:
            el = email.lower()
            if any(p in el for p in skip_patterns):
                continue
            if len(email) > 60:
                continue
            if '.' not in email.split('@')[1]:
                continue
            # Prefer business emails over provider domains
            domain_part = email.split('@')[1].lower()
            if any(x in domain_part for x in ['gmail', 'yahoo', 'hotmail', 'outlook', 'aol']):
                continue  # Skip personal emails
            return email, True
        
        if html:  # Found page but no business email
            break
    
    return None, False

# ── Email content ─────────────────────────────────────────────────────────────
EMAIL_TEMPLATES = {
    'dermatologist': {
        'subject': "Your {city} dermatology website — honest feedback",
        'body': """Hi {biz_name} team,

I was researching dermatology practices in {city} and came across your website.

Honest take: patients searching for a dermatologist in {city} right now are choosing between you and 3-4 competitors based almost entirely on their first 10 seconds on your site. Most practices lose real bookings to fixable issues they never notice.

I built a free tool that gives you a straight roast — no sugarcoating, no agency pitch — just honest feedback on what's working and what's costing you appointments:

👉 {roast_url}

Drop your URL, get a real scorecard in 60 seconds. No signup required.

Worth a look,
Rick
AI CEO, meetrick.ai

P.S. If your site scores under 60, I'll follow up personally with specific fixes."""
    },
    'med_spa': {
        'subject': "Your {city} med spa website — quick honest look",
        'body': """Hi {biz_name} team,

Was looking at med spas in {city} and wanted to give you something useful — an honest look at your website.

Med spa clients make their decision before they ever call you. They land on your site, spend about 8 seconds deciding if you look legit, and either bounce or book. Most sites have 2-3 fixable issues silently killing conversions.

I built a free roast tool for exactly this:

👉 {roast_url}

No forms, no upsell — just a real scorecard on your headline, CTAs, trust signals, and mobile experience. Takes 60 seconds.

Rick
meetrick.ai"""
    },
    'roofing': {
        'subject': "Your {city} roofing site — what homeowners actually see",
        'body': """Hi {biz_name} team,

Looked at your site while researching roofing companies in {city}. Wanted to share something useful.

When a homeowner has a leak or needs a new roof, they Google it, open 3-4 tabs, and pick the company that looks most trustworthy in the first 10 seconds. Most roofing sites lose that race on the homepage alone.

I built a free website roast tool — honest feedback, no agency fluff:

👉 {roast_url}

Paste your URL, get a real scorecard. Takes 60 seconds and might explain why some leads don't call back.

Rick
meetrick.ai"""
    },
    'physical_therapist': {
        'subject': "Your {city} PT website — what patients see first",
        'body': """Hi {biz_name} team,

Found your practice while looking at physical therapists in {city} — wanted to give you something actually useful.

Patients searching for PT in {city} are comparing you against other practices in 10 seconds flat. A confusing headline, no clear booking path, or a slow mobile load can cost you real appointments every week.

Free roast tool — honest scorecard, no sales pitch:

👉 {roast_url}

Drop your URL and see exactly what's working and what isn't. 60 seconds.

Rick
meetrick.ai"""
    },
    'financial_advisor': {
        'subject': "Your {city} advisory website — what prospects notice",
        'body': """Hi {biz_name} team,

Was looking at financial advisors in {city} and wanted to pass something along.

When someone is ready to work with an advisor, they do their research first. Your website is usually their first impression — and most advisory sites either look dated, say nothing specific, or bury the trust signals that actually convert prospects.

I built a free roast tool for small business websites:

👉 {roast_url}

Honest scorecard — headline, CTA, mobile, trust signals. No signup, no pitch. 60 seconds.

Rick
meetrick.ai"""
    },
}

def build_email(biz_name, cat_key, city, state):
    template = EMAIL_TEMPLATES.get(cat_key, {
        'subject': "Your {city} business website — honest feedback",
        'body': "Hi {biz_name} team,\n\nFree website roast at {roast_url}\n\nRick\nmeetrick.ai"
    })
    fmt = dict(biz_name=biz_name, city=city, state=state, roast_url=ROAST_URL)
    return template['subject'].format(**fmt), template['body'].format(**fmt)

# ── Send via Resend ────────────────────────────────────────────────────────────
def send_email(to_email, subject, body):
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        print(f"  BLOCKED: {block_reason}")
        return None
    if not RESEND_API_KEY:
        print("  ⚠️  RESEND_API_KEY missing")
        return None
    
    payload = json.dumps({
        "from": FROM_DISPLAY,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }).encode('utf-8')
    
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return result.get("id")
    except urllib.error.HTTPError as e:
        err = e.read().decode('utf-8', errors='ignore')[:200]
        print(f"  ❌ Resend {e.code}: {err}")
        return None
    except Exception as e:
        print(f"  ❌ Send error: {e}")
        return None

# ── Log ───────────────────────────────────────────────────────────────────────
def log_contact(biz_name, cat_key, city, state, email, domain, website, resend_id, confirmed):
    os.makedirs(os.path.dirname(PIPELINE_FILE), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "stage": "contacted",
        "status": "sent",
        "business_name": biz_name,
        "category": cat_key,
        "city": city,
        "state": state,
        "email": email,
        "email_confirmed": confirmed,
        "domain": domain,
        "website": website,
        "source": "cold_outreach_blast",
        "resend_id": resend_id,
    }
    with open(PIPELINE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"  📝 Logged to pipeline")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🚀 Lead Scrape + Blast — May 10, 2026")
    print("=" * 55)
    
    if not RESEND_API_KEY:
        print("❌ RESEND_API_KEY not set — aborting")
        return 0
    
    existing = load_existing_domains()
    print(f"📋 {len(existing)} domains already in pipeline\n")
    
    sent = 0
    results = []
    
    for query, city, state, cat_key in SEARCHES:
        if sent >= TARGET_COUNT:
            print(f"\n🎯 Hit target of {TARGET_COUNT} sends")
            break
        
        print(f"\n📍 {city}, {state} — {cat_key}")
        
        urls = search_bing(query, max_results=5)
        
        if not urls:
            print(f"  ⚠️  No results")
            continue
        
        print(f"  📌 {len(urls)} candidates")
        
        for domain, url in urls:
            if sent >= TARGET_COUNT:
                break
            
            # Dedup
            if domain in existing:
                print(f"  ⏭️  Already hit: {domain}")
                continue
            
            print(f"  🌐 {url}")
            
            # Try to get email
            email, confirmed = extract_email(url)
            
            if email:
                print(f"  ✉️  Found: {email}")
            else:
                email = f"info@{domain}"
                print(f"  📧 Guessing: {email}")
            
            # Business name from domain
            biz_name = domain.split('.')[0]
            biz_name = re.sub(r'[-_]', ' ', biz_name).title()
            if biz_name.lower() in ['info', 'www', 'contact', 'home', 'mail']:
                biz_name = f"{city} {cat_key.replace('_', ' ').title()}"
            
            # Build + send
            subject, body = build_email(biz_name, cat_key, city, state)
            resend_id = send_email(email, subject, body)
            
            if resend_id:
                print(f"  ✅ Sent → {email} [{resend_id[:8]}...]")
                log_contact(biz_name, cat_key, city, state, email, domain, url, resend_id, confirmed)
                existing.add(domain)
                sent += 1
                results.append({
                    "biz": biz_name, "city": city, "cat": cat_key,
                    "email": email, "confirmed": confirmed, "id": resend_id
                })
                time.sleep(0.4)
            else:
                print(f"  ❌ Failed: {email}")
    
    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 55}")
    print(f"✅ COMPLETE — {sent}/{TARGET_COUNT} emails sent")
    print()
    for r in results:
        mark = "✓" if r['confirmed'] else "~"
        print(f"  {mark} {r['biz']} ({r['city']}, {r['cat']}) → {r['email']}")
    
    # Write to daily note
    today = datetime.now().strftime('%Y-%m-%d')
    daily_note = os.path.expanduser(f"~/rick-vault/memory/{today}.md")
    os.makedirs(os.path.dirname(daily_note), exist_ok=True)
    with open(daily_note, "a") as f:
        f.write(f"\n## Lead Blast Run — {datetime.now().strftime('%H:%M')} PT\n")
        f.write(f"- Sent: {sent} cold emails (target: {TARGET_COUNT})\n")
        for r in results:
            mark = "✓" if r['confirmed'] else "~"
            f.write(f"  - {mark} {r['biz']} ({r['city']}, {r['cat']}) → {r['email']}\n")
    
    return sent

if __name__ == "__main__":
    count = main()
    sys.exit(0 if count > 0 else 1)
