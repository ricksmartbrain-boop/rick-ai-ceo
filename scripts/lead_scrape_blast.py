#!/usr/bin/env python3
"""
Lead Scrape + Blast — May 2026
Searches Google via agent-browser for local businesses, extracts emails, sends cold roast emails.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

import urllib.request
import urllib.parse
from email_safety import block_reason_for_recipient

# ── Config ────────────────────────────────────────────────────────────────────
PIPELINE_FILE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "Rick <rick@meetrick.ai>"
ROAST_URL = "https://meetrick.ai/roast"
TARGET_COUNT = 15

# ── Targets: fresh city+category combos not yet hit ───────────────────────────
TARGETS = [
    ("Tampa", "FL", "dermatologist", "dermatologist"),
    ("Tampa", "FL", "roofing company", "roofing"),
    ("Memphis", "TN", "med spa", "med_spa"),
    ("Memphis", "TN", "roofing company", "roofing"),
    ("Albuquerque", "NM", "dermatologist", "dermatologist"),
    ("Louisville", "KY", "med spa", "med_spa"),
    ("Detroit", "MI", "financial advisor", "financial_advisor"),
    ("Cincinnati", "OH", "dermatologist", "dermatologist"),
    ("Sacramento", "CA", "physical therapist", "physical_therapist"),
    ("Baltimore", "MD", "financial advisor", "financial_advisor"),
    ("Columbus", "OH", "dermatologist", "dermatologist"),
    ("Cleveland", "OH", "med spa", "med_spa"),
]

# ── Load existing pipeline (dedup by domain) ──────────────────────────────────
def load_existing_domains():
    domains = set()
    if not os.path.exists(PIPELINE_FILE):
        return domains
    with open(PIPELINE_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("domain"):
                    domains.add(r["domain"].lower())
                if r.get("email") and "@" in r.get("email", ""):
                    domains.add(r["email"].split("@")[1].lower())
            except Exception:
                pass
    return domains

# ── agent-browser search ──────────────────────────────────────────────────────
def ab(args, timeout=30):
    """Run agent-browser command, return stdout."""
    cmd = ["agent-browser"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except subprocess.TimeoutExpired:
        return ""
    except Exception as e:
        return ""

def search_google_for_business(category, city, state):
    """Use agent-browser to search Google and extract website URLs."""
    query = f'best {category} in {city} {state} website'
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    
    print(f"  🔍 Searching: {query}")
    
    # Navigate
    ab(["open", search_url], timeout=15)
    time.sleep(2)
    ab(["wait", "--load", "networkidle"], timeout=10)
    
    # Get page HTML to extract URLs
    html_out = ab(["get", "html", "--json"], timeout=15)
    
    # Parse URLs from HTML - look for business website links
    urls = []
    
    # Try to get the full text/links from snapshot
    snap_out = ab(["snapshot", "--json"], timeout=15)
    
    # Extract URLs from the combined output
    combined = html_out + " " + snap_out
    
    # Look for result links - business websites (not google.com links)
    url_pattern = r'https?://(?!(?:www\.google\.|google\.|maps\.google\.|support\.google\.|accounts\.google\.|translate\.google\.))[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+(?:/[^\s"<>]*)?'
    raw_urls = re.findall(url_pattern, combined)
    
    # Filter to likely business websites
    skip_domains = {
        'yelp.com', 'facebook.com', 'instagram.com', 'twitter.com', 
        'linkedin.com', 'youtube.com', 'healthgrades.com', 'zocdoc.com',
        'vitals.com', 'webmd.com', 'doximity.com', 'ratemds.com',
        'angi.com', 'homeadvisor.com', 'thumbtack.com', 'houzz.com',
        'bbb.org', 'angieslist.com', 'bark.com', 'nextdoor.com',
        'wordpress.com', 'wix.com', 'squarespace.com', 'godaddy.com',
        'w3.org', 'schema.org', 'gstatic.com', 'googleapis.com',
        'cdn.', 'static.', 'img.', 'images.',
    }
    
    seen = set()
    for url in raw_urls:
        # Extract base domain
        m = re.match(r'https?://([^/]+)', url)
        if not m:
            continue
        domain = m.group(1).lstrip('www.')
        
        # Skip aggregators and social
        skip = False
        for sd in skip_domains:
            if sd in domain:
                skip = True
                break
        if skip:
            continue
        
        # Deduplicate
        if domain in seen:
            continue
        seen.add(domain)
        
        # Keep clean root URL
        clean_url = f"https://www.{domain}" if not url.startswith('https://www.') else re.match(r'https?://[^/]+', url).group(0)
        urls.append((domain, clean_url))
        
        if len(urls) >= 4:
            break
    
    return urls

# ── Email extraction from website ─────────────────────────────────────────────
def extract_email_from_website(url):
    """Fetch website contact page and extract email."""
    
    def try_fetch(target_url, timeout=10):
        try:
            req = urllib.request.Request(
                target_url, 
                headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except Exception:
            return ""
    
    # Try main page + contact page
    html = try_fetch(url)
    if not html:
        html = try_fetch(url.rstrip('/') + '/contact', timeout=8)
    if not html:
        html = try_fetch(url.rstrip('/') + '/contact-us', timeout=8)
    
    if not html:
        return None
    
    # Extract emails
    emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
    
    # Filter out generic/invalid
    skip_patterns = ['example.', 'placeholder', 'your@', 'email@email', 
                     'sentry.', 'wixpress.', 'schema.org', '.png', '.jpg',
                     'noreply', 'no-reply', 'donotreply', 'wordpress',
                     'w3.org', 'gravatar', 'jquery']
    
    for email in emails:
        email_lower = email.lower()
        if any(p in email_lower for p in skip_patterns):
            continue
        if len(email) > 60:
            continue
        # Prefer info@, contact@, hello@, owner@, team@ 
        return email
    
    return None

# ── Email content generator ────────────────────────────────────────────────────
def build_email(business_name, category, city, state, website):
    cat_map = {
        'dermatologist': ('dermatology practice', 'patients are Googling', 'booking consultations'),
        'med_spa': ('med spa', 'clients are scrolling', 'booking treatments'),
        'roofing': ('roofing company', 'homeowners are searching', 'getting quotes'),
        'physical_therapist': ('PT practice', 'patients are searching', 'booking appointments'),
        'financial_advisor': ('financial advisory firm', 'prospects are researching', 'scheduling consultations'),
    }
    
    biz_type, action, cta_action = cat_map.get(category, ('business', 'customers are searching', 'taking action'))
    
    subject_options = {
        'dermatologist': f"Your {city} dermatology website — honest feedback",
        'med_spa': f"Your {city} med spa website — quick honest look",
        'roofing': f"Your {city} roofing site — what homeowners actually see",
        'physical_therapist': f"Your {city} PT website — what patients see first",
        'financial_advisor': f"Your {city} advisory website — what prospects notice",
    }
    subject = subject_options.get(category, f"Your {city} business website — quick honest feedback")
    
    body = f"""Hi {business_name} team,

I stumbled on your site while looking at {biz_type}s in {city} — I run a free website roast tool for local businesses and wanted to give you an honest look.

Here's the thing: {action} for exactly what you offer right now. Your website is either pulling them in or pushing them away. Most small business sites I see have 2-3 fixable issues costing them real bookings every week — things like unclear CTAs, slow mobile load, or a headline that doesn't say what the business actually does.

I built a free tool that gives you an honest roast (not sugarcoated agency fluff) in 60 seconds:

👉 {ROAST_URL}

Just drop your URL and get a real scorecard. No sales pitch, no signup required — just straight feedback on what's working and what's losing you {cta_action}.

Worth 60 seconds,
Rick
AI CEO, meetrick.ai

P.S. If your site scores under 60, I'll personally follow up with specific fixes.
"""
    
    return subject, body

# ── Send via Resend ────────────────────────────────────────────────────────────
def send_email(to_email, subject, body):
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        print(f"  BLOCKED: {block_reason}")
        return None
    if not RESEND_API_KEY:
        print("  ⚠️  No RESEND_API_KEY — skipping send")
        return None
    
    payload = json.dumps({
        "from": FROM_EMAIL,
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
        err_body = e.read().decode('utf-8', errors='ignore')
        print(f"  ❌ Resend error {e.code}: {err_body[:200]}")
        return None
    except Exception as e:
        print(f"  ❌ Send error: {e}")
        return None

# ── Log to pipeline ────────────────────────────────────────────────────────────
def log_contact(business_name, category, city, state, email, domain, website, resend_id, email_confirmed):
    os.makedirs(os.path.dirname(PIPELINE_FILE), exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "stage": "contacted",
        "status": "sent",
        "business_name": business_name,
        "category": category,
        "city": city,
        "state": state,
        "email": email,
        "email_confirmed": email_confirmed,
        "domain": domain,
        "website": website,
        "source": "cold_outreach_blast",
        "resend_id": resend_id,
    }
    with open(PIPELINE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── Extract business name from domain/URL ─────────────────────────────────────
def domain_to_name(domain, city):
    """Best-effort business name from domain."""
    name = domain.split('.')[0]
    name = re.sub(r'[-_]', ' ', name)
    name = name.title()
    # Remove common generic words if name is too generic
    if name.lower() in ['info', 'contact', 'home', 'www']:
        name = f"{city} Business"
    return name

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🚀 Lead Scrape + Blast — May 10, 2026")
    print("=" * 50)
    
    existing_domains = load_existing_domains()
    print(f"📋 {len(existing_domains)} domains already in pipeline\n")
    
    sent_count = 0
    results = []
    
    for city, state, search_term, cat_key in TARGETS:
        if sent_count >= TARGET_COUNT:
            break
        
        print(f"\n📍 {city}, {state} — {search_term}")
        
        urls = search_google_for_business(search_term, city, state)
        
        if not urls:
            print(f"  ⚠️  No URLs found")
            continue
        
        for domain, url in urls:
            if sent_count >= TARGET_COUNT:
                break
            
            # Dedup check
            if domain.lower() in existing_domains:
                print(f"  ⏭️  Already contacted: {domain}")
                continue
            
            print(f"  🌐 Checking: {url}")
            
            # Extract email
            email = extract_email_from_website(url)
            
            if not email:
                print(f"  ❌ No email found on {domain}")
                # Try a guessed email anyway
                email = f"info@{domain}"
                email_confirmed = False
            else:
                email_confirmed = True
                print(f"  ✉️  Found: {email}")
            
            # Build business name
            biz_name = domain_to_name(domain, city)
            
            # Build email
            subject, body = build_email(biz_name, cat_key, city, state, url)
            
            # Send
            resend_id = send_email(email, subject, body)
            
            if resend_id:
                print(f"  ✅ Sent to {email} (id: {resend_id})")
                log_contact(biz_name, cat_key, city, state, email, domain, url, resend_id, email_confirmed)
                existing_domains.add(domain.lower())
                sent_count += 1
                results.append({
                    "business": biz_name, "city": city, "category": cat_key,
                    "email": email, "confirmed": email_confirmed
                })
                time.sleep(0.3)  # Rate limit buffer
            else:
                print(f"  ❌ Send failed for {email}")
    
    # Summary
    print(f"\n{'=' * 50}")
    print(f"✅ DONE — {sent_count} emails sent this run")
    for r in results:
        conf = "✓" if r['confirmed'] else "~"
        print(f"  {conf} {r['business']} ({r['city']}) → {r['email']}")
    
    # Write summary to daily note
    today = datetime.now().strftime('%Y-%m-%d')
    daily_note = os.path.expanduser(f"~/rick-vault/memory/{today}.md")
    summary = f"\n## Lead Blast Run — {datetime.now().strftime('%H:%M')}\n"
    summary += f"- Sent: {sent_count} cold emails\n"
    for r in results:
        conf = "✓" if r['confirmed'] else "~"
        summary += f"  - {conf} {r['business']} ({r['city']}, {r['category']}) → {r['email']}\n"
    
    os.makedirs(os.path.dirname(daily_note), exist_ok=True)
    with open(daily_note, "a") as f:
        f.write(summary)
    
    return sent_count

if __name__ == "__main__":
    count = main()
    sys.exit(0 if count > 0 else 1)
