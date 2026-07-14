#!/usr/bin/env python3
"""
Lead Blast — Direct URL approach.
Uses curated + discovered business URLs, verifies each, extracts emails, sends cold roast.
"""

import json, os, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from email_safety import block_reason_for_recipient
import warnings
warnings.filterwarnings('ignore')  # suppress LibreSSL warning
try:
    import requests as _requests
    USE_REQUESTS = True
except ImportError:
    USE_REQUESTS = False

PIPELINE_FILE = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_DISPLAY = "Rick <rick@meetrick.ai>"
ROAST_URL = "https://meetrick.ai/roast"
TARGET_COUNT = 14

# ── Curated business candidates ───────────────────────────────────────────────
# Format: (domain, display_name_hint, city, state, cat_key)
# From first working Bing search (Tampa derm) + curated knowledge + plausible guesses
CANDIDATES = [
    # Tampa, FL — Dermatologist (from working Bing search)
    ("tbderm.com", "Tampa Bay Dermatology", "Tampa", "FL", "dermatologist"),
    ("suncoastskin.com", "Suncoast Skin Solutions", "Tampa", "FL", "dermatologist"),
    ("skinhealthforever.com", "Skin Health Forever", "Tampa", "FL", "dermatologist"),
    ("themodernderm.com", "The Modern Derm", "Tampa", "FL", "dermatologist"),

    # Tampa, FL — Roofing
    ("tamparoofingpros.com", "Tampa Roofing Pros", "Tampa", "FL", "roofing"),
    ("bayroofingtampa.com", "Bay Roofing Tampa", "Tampa", "FL", "roofing"),
    ("suncoastroofing.com", "Suncoast Roofing", "Tampa", "FL", "roofing"),
    ("buildingconceptsfl.com", "Building Concepts FL", "Tampa", "FL", "roofing"),
    ("precisionroofingfl.com", "Precision Roofing FL", "Tampa", "FL", "roofing"),
    ("irooffl.com", "iRoof FL", "Tampa", "FL", "roofing"),

    # Memphis, TN — Med Spa
    ("amfmedspa.com", "AMF Med Spa", "Memphis", "TN", "med_spa"),
    ("chadwickmedspa.com", "Chadwick Med Spa", "Memphis", "TN", "med_spa"),
    ("memphismedspa.com", "Memphis Med Spa", "Memphis", "TN", "med_spa"),
    ("bellaestheticstn.com", "Bella Aesthetics TN", "Memphis", "TN", "med_spa"),
    ("skinrevolutionmedspa.com", "Skin Revolution Med Spa", "Memphis", "TN", "med_spa"),
    ("skinskippermedspa.com", "Skin Skipper", "Memphis", "TN", "med_spa"),

    # Memphis, TN — Roofing
    ("memphisroofingpros.com", "Memphis Roofing Pros", "Memphis", "TN", "roofing"),
    ("southernroofingmemphis.com", "Southern Roofing Memphis", "Memphis", "TN", "roofing"),
    ("americanroofingmemphis.com", "American Roofing Memphis", "Memphis", "TN", "roofing"),
    ("relianceroofing.com", "Reliance Roofing", "Memphis", "TN", "roofing"),
    ("craftmasterroofing.com", "Craftmaster Roofing", "Memphis", "TN", "roofing"),

    # Albuquerque, NM — Dermatologist
    ("abqderm.com", "ABQ Derm", "Albuquerque", "NM", "dermatologist"),
    ("desertdermatologyabq.com", "Desert Dermatology ABQ", "Albuquerque", "NM", "dermatologist"),
    ("swderm.com", "Southwest Dermatology", "Albuquerque", "NM", "dermatologist"),
    ("newmexicoderm.com", "New Mexico Derm", "Albuquerque", "NM", "dermatologist"),
    ("sundancederm.com", "Sundance Dermatology", "Albuquerque", "NM", "dermatologist"),
    ("abqskincare.com", "ABQ Skin Care", "Albuquerque", "NM", "dermatologist"),

    # Albuquerque, NM — Roofing
    ("abqroofingpros.com", "ABQ Roofing Pros", "Albuquerque", "NM", "roofing"),
    ("desertroofing.com", "Desert Roofing", "Albuquerque", "NM", "roofing"),
    ("newmexicoroofer.com", "New Mexico Roofer", "Albuquerque", "NM", "roofing"),
    ("mountainviewroofing.com", "Mountain View Roofing", "Albuquerque", "NM", "roofing"),

    # Louisville, KY — Med Spa
    ("louisvillelaserspa.com", "Louisville Laser Spa", "Louisville", "KY", "med_spa"),
    ("bluegrassmedspa.com", "Bluegrass Med Spa", "Louisville", "KY", "med_spa"),
    ("dermalivemedspa.com", "DermaLive Med Spa", "Louisville", "KY", "med_spa"),
    ("glowmedspalouisville.com", "Glow Med Spa Louisville", "Louisville", "KY", "med_spa"),
    ("the-aesthetics-lounge.com", "The Aesthetics Lounge", "Louisville", "KY", "med_spa"),

    # Louisville, KY — Physical Therapist
    ("bluegrasspt.com", "Bluegrass PT", "Louisville", "KY", "physical_therapist"),
    ("loupt.com", "Lou PT", "Louisville", "KY", "physical_therapist"),
    ("louisvillept.com", "Louisville PT", "Louisville", "KY", "physical_therapist"),
    ("kentuckianaphysicaltherapy.com", "Kentuckiana PT", "Louisville", "KY", "physical_therapist"),

    # Detroit, MI — Financial Advisor
    ("detroitwealthmgmt.com", "Detroit Wealth Management", "Detroit", "MI", "financial_advisor"),
    ("michigancapitalgroup.com", "Michigan Capital Group", "Detroit", "MI", "financial_advisor"),
    ("greatlakedfinancial.com", "Great Lakes Financial", "Detroit", "MI", "financial_advisor"),
    ("detroitfinancialgroup.com", "Detroit Financial Group", "Detroit", "MI", "financial_advisor"),
    ("peninsulafinancialgroup.com", "Peninsula Financial Group", "Detroit", "MI", "financial_advisor"),

    # Cincinnati, OH — Dermatologist
    ("cincyderm.com", "Cincy Derm", "Cincinnati", "OH", "dermatologist"),
    ("tristatedermatology.com", "Tristate Dermatology", "Cincinnati", "OH", "dermatologist"),
    ("skincarecincinnati.com", "Skincare Cincinnati", "Cincinnati", "OH", "dermatologist"),
    ("eastcinderm.com", "East Cin Derm", "Cincinnati", "OH", "dermatologist"),
    ("queencityderm.com", "Queen City Derm", "Cincinnati", "OH", "dermatologist"),

    # Sacramento, CA — Physical Therapist
    ("sacramentopt.com", "Sacramento PT", "Sacramento", "CA", "physical_therapist"),
    ("capitalpt.com", "Capital PT", "Sacramento", "CA", "physical_therapist"),
    ("sacrehab.com", "Sac Rehab", "Sacramento", "CA", "physical_therapist"),
    ("goldenhillspt.com", "Golden Hills PT", "Sacramento", "CA", "physical_therapist"),
    ("broadwayphysicaltherapy.com", "Broadway Physical Therapy", "Sacramento", "CA", "physical_therapist"),

    # Baltimore, MD — Financial Advisor
    ("chesapeakefinancialadvisors.com", "Chesapeake Financial Advisors", "Baltimore", "MD", "financial_advisor"),
    ("baltimorewealthmgmt.com", "Baltimore Wealth Mgmt", "Baltimore", "MD", "financial_advisor"),
    ("harborwealthadvisors.com", "Harbor Wealth Advisors", "Baltimore", "MD", "financial_advisor"),
    ("innerharborfin.com", "Inner Harbor Financial", "Baltimore", "MD", "financial_advisor"),

    # Columbus, OH — Dermatologist
    ("columbusdermatologyassociates.com", "Columbus Derm Associates", "Columbus", "OH", "dermatologist"),
    ("centralohioderm.com", "Central Ohio Derm", "Columbus", "OH", "dermatologist"),
    ("columbusderm.com", "Columbus Derm", "Columbus", "OH", "dermatologist"),
    ("buckeyderm.com", "Buckeye Derm", "Columbus", "OH", "dermatologist"),

    # Cleveland, OH — Med Spa
    ("clevelandmedspa.com", "Cleveland Med Spa", "Cleveland", "OH", "med_spa"),
    ("lakewoodmedspa.com", "Lakewood Med Spa", "Cleveland", "OH", "med_spa"),
    ("theglowroomcleveland.com", "The Glow Room Cleveland", "Cleveland", "OH", "med_spa"),
    ("lakesidemedspa.com", "Lakeside Med Spa", "Cleveland", "OH", "med_spa"),
    ("foreverglowmedspa.com", "Forever Glow Med Spa", "Cleveland", "OH", "med_spa"),
]

# ── Load existing pipeline ────────────────────────────────────────────────────
def load_existing():
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

# ── Fetch + verify URL ────────────────────────────────────────────────────────
def verify_and_get_email(domain):
    """Try to fetch the website and extract an email. Returns (exists, email, confirmed)."""
    
    urls_to_try = [
        f"https://www.{domain}",
        f"https://{domain}",
        f"http://www.{domain}",
    ]
    
    skip_emails = [
        'sentry', 'wixpress', 'schema', 'example', 'placeholder',
        'noreply', 'no-reply', 'donotreply', 'wordpress', 'gravatar',
        'jquery', '.png', '.jpg', '.gif',
    ]
    
    for base_url in urls_to_try[:2]:  # Try https first
        for path in ['', '/contact', '/contact-us', '/about']:
            try:
                req = urllib.request.Request(
                    base_url.rstrip('/') + path,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    }
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    html = resp.read().decode('utf-8', errors='ignore')
                    
                    # Decode HTML entities
                    html = html.replace('%40', '@').replace('&#64;', '@').replace('&amp;', '&')
                    
                    emails = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', html)
                    
                    for email in emails:
                        el = email.lower()
                        if any(p in el for p in skip_emails):
                            continue
                        if len(email) > 60:
                            continue
                        if '.' not in email.split('@')[1]:
                            continue
                        # Skip generic provider domains
                        parts = email.split('@')
                        if any(x in parts[1].lower() for x in ['gmail', 'yahoo', 'hotmail', 'outlook', 'aol', 'icloud']):
                            continue
                        return True, email, True
                    
                    # Site exists but no business email found
                    if html and len(html) > 500:
                        return True, f"info@{domain}", False
                    
            except urllib.error.HTTPError as e:
                if e.code == 404 and path != '':
                    continue
                if e.code in (200, 301, 302, 403):
                    return True, f"info@{domain}", False
                continue
            except Exception:
                continue
    
    return False, None, False

# ── Email templates ───────────────────────────────────────────────────────────
TEMPLATES = {
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

When a homeowner needs a new roof or has storm damage, they Google it, open 3-4 tabs, and pick the company that looks most trustworthy in the first 10 seconds. Most roofing sites lose that race on the homepage alone.

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

Patients searching for PT in {city} are comparing you against other practices in 10 seconds flat. A confusing headline, no clear booking path, or slow mobile load can cost you real appointments every week.

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
    t = TEMPLATES.get(cat_key, {
        'subject': "Your {city} website — honest feedback",
        'body': "Hi {biz_name},\n\nFree website roast at {roast_url}\n\nRick\nmeetrick.ai"
    })
    fmt = dict(biz_name=biz_name, city=city, state=state, roast_url=ROAST_URL)
    return t['subject'].format(**fmt), t['body'].format(**fmt)

# ── Send via Resend ────────────────────────────────────────────────────────────
def send_email(to_email, subject, body):
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        print(f"  BLOCKED: {block_reason}")
        return None
    if not RESEND_API_KEY:
        print("  ⚠️  No RESEND_API_KEY")
        return None
    
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": FROM_DISPLAY,
        "to": [to_email],
        "subject": subject,
        "text": body,
    }
    
    if USE_REQUESTS:
        try:
            resp = _requests.post(
                "https://api.resend.com/emails",
                json=payload,
                headers=headers,
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json().get("id")
            else:
                print(f"  ❌ Resend {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"  ❌ {e}")
            return None
    else:
        # Fallback: curl subprocess
        import subprocess
        try:
            result = subprocess.run(
                ['curl', '-s', '-X', 'POST', 'https://api.resend.com/emails',
                 '-H', f'Authorization: Bearer {RESEND_API_KEY}',
                 '-H', 'Content-Type: application/json',
                 '-d', json.dumps(payload)],
                capture_output=True, text=True, timeout=20
            )
            data = json.loads(result.stdout)
            return data.get('id')
        except Exception as e:
            print(f"  ❌ curl: {e}")
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
        "website": f"https://www.{domain}",
        "source": "cold_outreach_blast",
        "resend_id": resend_id,
    }
    with open(PIPELINE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    print("🚀 Lead Blast — Direct URL Mode — May 10, 2026")
    print("=" * 55)
    
    if not RESEND_API_KEY:
        print("❌ RESEND_API_KEY missing")
        return 0
    
    existing = load_existing()
    print(f"📋 {len(existing)} domains already in pipeline")
    print(f"🎯 Targeting {TARGET_COUNT} new sends\n")
    
    sent = 0
    results = []
    
    # Track which city+cat combos we've sent to
    sent_combos = set()
    
    for domain, display_name, city, state, cat_key in CANDIDATES:
        if sent >= TARGET_COUNT:
            break
        
        combo = f"{city}|{cat_key}"
        
        # Skip already-contacted domains
        if domain.lower() in existing:
            continue
        
        print(f"🔎 {display_name} ({city}, {cat_key})")
        
        # Verify the site exists + get email
        exists, email, confirmed = verify_and_get_email(domain)
        
        if not exists:
            print(f"  💀 Site not reachable: {domain}")
            time.sleep(0.2)
            continue
        
        if confirmed:
            print(f"  ✉️  Email found: {email}")
        else:
            print(f"  📧 Using: {email} (guessed)")
        
        # Build + send email
        subject, body = build_email(display_name, cat_key, city, state)
        resend_id = send_email(email, subject, body)
        
        if resend_id:
            print(f"  ✅ Sent! [{resend_id[:8]}...]")
            log_contact(display_name, cat_key, city, state, email, domain,
                       f"https://www.{domain}", resend_id, confirmed)
            existing.add(domain.lower())
            sent += 1
            sent_combos.add(combo)
            results.append({
                "biz": display_name, "city": city, "cat": cat_key,
                "email": email, "confirmed": confirmed
            })
            time.sleep(0.5)
        else:
            print(f"  ❌ Send failed")
    
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
        f.write(f"- Mode: direct URL verification\n")
        for r in results:
            mark = "✓" if r['confirmed'] else "~"
            f.write(f"  - {mark} {r['biz']} ({r['city']}, {r['cat']}) → {r['email']}\n")
    
    return sent

if __name__ == "__main__":
    count = main()
    sys.exit(0 if count > 0 else 1)
