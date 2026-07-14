#!/usr/bin/env python3
"""
new-leads-pipeline.py — Scrape new business leads and send cold roast outreach.
Chains: google-maps-scraper.py → roast-site.py → Resend cold email
Deduplicates against existing pipeline.jsonl entries.

Usage:
  python3 new-leads-pipeline.py --cities "Tampa,Orlando,Sacramento" --categories "med spa,dentist" --per-combo 5 --send
"""

import json, os, sys, subprocess, argparse, datetime, time
from pathlib import Path
from email_safety import block_reason_for_recipient

PIPELINE_LOG = Path.home() / "rick-vault/logs/pipeline.jsonl"
SCRAPER = Path.home() / "clawd/scripts/google-maps-scraper.py"
ROAST = Path.home() / "clawd/scripts/roast-site.py"
ENV_FILE = Path.home() / "clawd/config/rick.env"
FROM = "Rick <rick@meetrick.ai>"
DELAY = 1.5

BLOCKED_EMAILS = {
    "user@domain.com", "FULL-WHITE@3x.png", "rick@meetrick.ai",
    "vladislav@belkins.io", "vlad@belkins.io",
    "paul25011991z@gmail.com",
}

def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "): line = line[7:]
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def existing_emails():
    emails = set()
    if PIPELINE_LOG.exists():
        for line in PIPELINE_LOG.read_text().splitlines():
            line = line.strip()
            if not line: continue
            try:
                d = json.loads(line)
                if d.get("email"):
                    emails.add(d["email"].lower())
            except: pass
    return emails

def scrape_businesses(city, category, count):
    """Search for business websites using Google Places API or web search fallback."""
    import urllib.request, urllib.parse, re

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    query = f"{category} in {city}"

    if api_key:
        try:
            url = f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={urllib.parse.quote(query)}&key={api_key}"
            resp = json.loads(urllib.request.urlopen(urllib.request.Request(url), timeout=15).read())
            results = []
            for place in resp.get("results", [])[:count]:
                place_id = place.get("place_id")
                detail_url = f"https://maps.googleapis.com/maps/api/place/details/json?place_id={place_id}&fields=name,website,formatted_phone_number,rating,user_ratings_total&key={api_key}"
                detail = json.loads(urllib.request.urlopen(urllib.request.Request(detail_url), timeout=10).read())
                r = detail.get("result", {})
                if r.get("website"):
                    results.append({
                        "name": r.get("name", ""),
                        "website": r.get("website", ""),
                        "phone": r.get("formatted_phone_number", ""),
                        "rating": r.get("rating", 0),
                        "reviews": r.get("user_ratings_total", 0),
                    })
            if results:
                return results
        except Exception as e:
            print(f"  Places API error: {e}")

    skip_domains = {
        "duckduckgo.com", "duck.co", "wikipedia.org", "yelp.com", "yellowpages.com",
        "facebook.com", "instagram.com", "twitter.com", "linkedin.com", "reddit.com",
        "mapquest.com", "bbb.org", "indeed.com", "healthgrades.com", "zocdoc.com",
        "local.yahoo.com", "search.yahoo.com", "yahoo.com", "bing.com"
    }

    def normalize_candidate_url(raw_url):
        if not raw_url:
            return None
        raw_url = raw_url.replace("&amp;", "&")
        parsed = urllib.parse.urlparse(raw_url)
        if "search.yahoo.com" in parsed.netloc:
            target = urllib.parse.parse_qs(parsed.query).get("RU", [""])[0]
            if not target:
                m = re.search(r'/RU=([^/]+)/RK=', raw_url)
                if m:
                    target = m.group(1)
            raw_url = urllib.parse.unquote(target) if target else raw_url
            parsed = urllib.parse.urlparse(raw_url)
        domain = parsed.netloc.lower().replace("www.", "")
        if not domain:
            return None
        if domain in skip_domains or any(domain.endswith(f'.{blocked}') for blocked in skip_domains):
            return None
        if parsed.scheme not in ("http", "https"):
            raw_url = f"https://{domain}"
        return domain, raw_url

    # Fallback 1: DuckDuckGo HTML, unless it bot-challenges us
    try:
        search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(f'{category} {city} contact email site')}"
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        if "anomaly-modal" not in html and "Unfortunately, bots use DuckDuckGo too" not in html:
            urls = re.findall(r'class="result__url"[^>]*href="([^"]+)"', html)
            if not urls:
                urls = re.findall(r'nofollow" class="result__a" href="([^"]+)"', html)
            unique = []
            seen = set()
            for u in urls:
                normalized = normalize_candidate_url(u)
                if not normalized:
                    continue
                domain, website = normalized
                if domain not in seen:
                    seen.add(domain)
                    unique.append({"name": domain, "website": website, "phone": "", "rating": 0, "reviews": 0})
            if unique:
                return unique[:count]
        else:
            print("  DuckDuckGo bot challenge hit, falling back to Yahoo search")
    except Exception as e:
        print(f"  DuckDuckGo fallback error: {e}")

    # Fallback 2: Yahoo Search result links, decode target site from RU parameter
    try:
        search_url = f"https://search.yahoo.com/search?p={urllib.parse.quote(f'{category} {city} website')}"
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        urls = re.findall(r'href="(https://r\.search\.yahoo\.com/[^"]*?/secA3Ny/[^"]+)"', html)
        if not urls:
            urls = re.findall(r'href="(https://r\.search\.yahoo\.com/[^"]+)"', html)
        unique = []
        seen = set()
        for u in urls:
            normalized = normalize_candidate_url(u)
            if not normalized:
                continue
            domain, website = normalized
            if domain not in seen:
                seen.add(domain)
                unique.append({"name": domain, "website": website, "phone": "", "rating": 0, "reviews": 0})
        return unique[:count]
    except Exception as e:
        print(f"  Web search error: {e}")
        return []

def extract_email_from_site(url):
    """Try to find an email from a business website."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "10", url],
            capture_output=True, text=True, timeout=15
        )
        import re
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', result.stdout)
        # Filter out junk
        for e in emails:
            e_lower = e.lower()
            if any(x in e_lower for x in ['@sentry', '@example', '@test', '@wix', '@squarespace', 
                                            '@wordpress', '@google', '.png', '.jpg', '.svg', '.gif',
                                            '.webp', '.jpeg', '.css', '.js', '@2x', '@3x',
                                            'noreply', 'unsubscribe', 'privacy', 'donotreply',
                                            'your@email', 'you@email', 'info@example', 'name@domain',
                                            'email@domain', 'user@', 'ajax', 'loader']):
                continue
            if e_lower not in BLOCKED_EMAILS:
                return e
        return None
    except:
        return None

def roast_site(url):
    """Generate a roast for a business site."""
    try:
        result = subprocess.run(
            ["python3", str(ROAST), "--url", url, "--json"],
            capture_output=True, text=True, timeout=60
        )
        try:
            return json.loads(result.stdout)
        except:
            return {"summary": result.stdout[:500]}
    except Exception as e:
        return {"error": str(e)}

def send_cold_email(to, biz_name, city, category, roast_summary=None):
    block_reason = block_reason_for_recipient(to)
    if block_reason:
        return False, block_reason
    key = os.environ.get("RESEND_API_KEY", "")
    if not key: return False, "no key"
    
    clean_name = biz_name.replace("https://", "").replace("http://", "").strip("/")
    subject = f"Quick website audit — {clean_name}"
    
    roast_line = ""
    if roast_summary:
        roast_line = f"\n\nQuick observation: {roast_summary[:200]}\n"
    
    body = f"""Hi,

I ran your website ({clean_name}) through an AI audit tool I built. Found a few things worth looking at.{roast_line}

The full free audit takes 60 seconds: meetrick.ai/roast

Most {category} sites look credible but don't convert. The fix is usually copy and CTA placement, not a redesign.

If the roast resonates and you want help fixing it — that's what I do. But the audit is free, no strings.

— Rick
AI CEO, meetrick.ai

Reply "stop" to opt out."""

    payload = json.dumps({"from": FROM, "to": [to], "subject": subject, "text": body})
    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
         "-H", "Content-Type: application/json",
         "-H", f"Authorization: Bearer {key}", "-d", payload],
        capture_output=True, text=True, timeout=15
    )
    try:
        data = json.loads(result.stdout)
        if data.get("id"): return True, data["id"]
        return False, str(data)
    except:
        return False, result.stdout[:100]

def log_entry(email, stage, biz, city, category, extra=None):
    entry = {
        "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stage": stage,
        "source": "google_maps",
        "target": biz,
        "email": email,
        "channel": "cold_email",
        "city": city,
        "category": category,
    }
    if extra: entry.update(extra)
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities", required=True, help="Comma-separated cities")
    parser.add_argument("--categories", required=True, help="Comma-separated categories")
    parser.add_argument("--per-combo", type=int, default=5)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--limit", type=int, default=100, help="Max total emails to send")
    args = parser.parse_args()

    load_env()
    known = existing_emails()
    cities = [c.strip() for c in args.cities.split(",")]
    categories = [c.strip() for c in args.categories.split(",")]

    total_scraped = 0
    total_emailed = 0
    total_skipped = 0

    for city in cities:
        for cat in categories:
            if total_emailed >= args.limit:
                print(f"\n⚠️ Hit email limit ({args.limit})")
                break
                
            print(f"\n🔍 {cat} in {city} (limit {args.per_combo})")
            businesses = scrape_businesses(city, cat, args.per_combo)
            print(f"  Found: {len(businesses)} businesses")

            for biz in businesses:
                if total_emailed >= args.limit:
                    break
                    
                url = biz.get("website", "")
                name = biz.get("name", url)
                total_scraped += 1

                # Try to find email
                email = extract_email_from_site(url)
                if not email:
                    print(f"  ⏭️ No email found: {name}")
                    log_entry("", "no_email", url, city, cat)
                    continue

                if email.lower() in known or email in BLOCKED_EMAILS:
                    print(f"  ⏭️ Already known: {email}")
                    total_skipped += 1
                    continue

                known.add(email.lower())
                log_entry(email, "fetched", url, city, cat)

                if args.send:
                    print(f"  📧 Sending to {email} ({name})", end=" ")
                    ok, detail = send_cold_email(email, url, city, cat)
                    if ok:
                        log_entry(email, "contacted", url, city, cat, {"resend_id": detail, "score": biz.get("rating", 0)})
                        print("✅")
                        total_emailed += 1
                    else:
                        log_entry(email, "send_error", url, city, cat, {"error": detail})
                        print(f"❌ {detail}")
                    time.sleep(DELAY)
                else:
                    print(f"  📋 Would email: {email} ({name})")
                    total_emailed += 1

    print(f"\n📊 Summary:")
    print(f"  Scraped: {total_scraped}")
    print(f"  Emailed: {total_emailed}")
    print(f"  Skipped (dupe): {total_skipped}")

if __name__ == "__main__":
    main()
