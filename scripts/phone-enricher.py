#!/usr/bin/env python3
"""
phone-enricher.py — Fetch business homepages, extract phone numbers.
Outputs enriched leads to phone-leads.jsonl
"""
import json, re, subprocess, time
from pathlib import Path
from urllib.parse import urlparse

PIPELINE_LOG = Path.home() / "rick-vault/logs/pipeline.jsonl"
OUTPUT_FILE = Path.home() / "rick-vault/runtime/calls/phone-leads.jsonl"
MAX_LEADS = 30
DELAY = 0.8

# Phone patterns — US formats
PHONE_RE = re.compile(
    r'(?:tel:|href=["\']tel:)?'
    r'(\+?1?[\s.\-]?\(?[2-9]\d{2}\)?[\s.\-]?[2-9]\d{2}[\s.\-]?\d{4})',
    re.IGNORECASE
)

def clean_phone(raw):
    digits = re.sub(r'\D', '', raw)
    if digits.startswith('1') and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10:
        return None
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

def get_root_url(url):
    """Return the root domain URL."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/"

def fetch_page(url):
    """Fetch URL via curl with short timeout."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "8", "--user-agent",
             "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
             url],
            capture_output=True, text=True, timeout=12
        )
        return result.stdout
    except Exception as e:
        return ""

def extract_phones(html):
    """Extract unique phone numbers from HTML."""
    # Prefer tel: links first
    tel_links = re.findall(r'href=["\']tel:([^"\']+)["\']', html, re.IGNORECASE)
    phones = set()
    for raw in tel_links:
        p = clean_phone(raw)
        if p:
            phones.add(p)
    if phones:
        return list(phones)
    # Fall back to text patterns
    for raw in PHONE_RE.findall(html):
        p = clean_phone(raw)
        if p:
            phones.add(p)
    return list(phones)

def load_pipeline_leads():
    leads = {}
    for line in PIPELINE_LOG.read_text().splitlines():
        line = line.strip()
        if not line: continue
        try: d = json.loads(line)
        except: continue
        email = (d.get("email") or "").strip().lower()
        if not email or "@" not in email: continue
        website = d.get("website") or ""
        if not website: continue
        if email not in leads:
            leads[email] = {
                "business": d.get("business") or d.get("business_name") or "",
                "email": email,
                "website": website,
                "city": d.get("city", ""),
                "category": d.get("category", ""),
                "phone": d.get("phone") or d.get("phone_number") or "",
            }
        # Prefer shorter (root) website URL
        existing = leads[email]["website"]
        if len(website) < len(existing):
            leads[email]["website"] = website
    return leads

def load_existing_phone_leads():
    """Return set of emails already in phone-leads.jsonl"""
    existing = set()
    if OUTPUT_FILE.exists():
        for line in OUTPUT_FILE.read_text().splitlines():
            try:
                d = json.loads(line.strip())
                if d.get("email"):
                    existing.add(d["email"].lower())
            except: pass
    return existing

def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    leads = load_pipeline_leads()
    existing_emails = load_existing_phone_leads()
    
    # Prioritize leads not already processed
    to_process = [(e, l) for e, l in leads.items() 
                  if e not in existing_emails and not l["phone"]]
    
    print(f"📋 Leads needing phone lookup: {len(to_process)}")
    print(f"📂 Already in phone-leads.jsonl: {len(existing_emails)}")
    print(f"🔍 Processing up to {MAX_LEADS} leads...\n")
    
    found = 0
    not_found = 0
    errors = 0
    
    for i, (email, lead) in enumerate(to_process[:MAX_LEADS]):
        biz = lead["business"] or email
        root_url = get_root_url(lead["website"])
        print(f"  [{i+1}/{min(len(to_process), MAX_LEADS)}] {biz} → {root_url}", end=" ")
        
        html = fetch_page(root_url)
        if not html:
            print("❌ fetch failed")
            errors += 1
            continue
        
        phones = extract_phones(html)
        if phones:
            phone = phones[0]
            entry = {
                "business": lead["business"],
                "phone": phone,
                "website": lead["website"],
                "city": lead["city"],
                "category": lead["category"],
                "email": email,
            }
            with open(OUTPUT_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"✅ {phone}")
            found += 1
        else:
            # Try contact page
            contact_url = root_url.rstrip("/") + "/contact"
            html2 = fetch_page(contact_url)
            phones2 = extract_phones(html2) if html2 else []
            if phones2:
                phone = phones2[0]
                entry = {
                    "business": lead["business"],
                    "phone": phone,
                    "website": lead["website"],
                    "city": lead["city"],
                    "category": lead["category"],
                    "email": email,
                }
                with open(OUTPUT_FILE, "a") as f:
                    f.write(json.dumps(entry) + "\n")
                print(f"✅ (contact page) {phone}")
                found += 1
            else:
                print("— no phone found")
                not_found += 1
        
        time.sleep(DELAY)
    
    print(f"\n📊 Phone Enrichment Results:")
    print(f"  ✅ Phones found: {found}")
    print(f"  — Not found: {not_found}")
    print(f"  ❌ Errors: {errors}")
    print(f"  📁 Written to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
