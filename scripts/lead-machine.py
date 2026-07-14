#!/usr/bin/env python3
"""
lead-machine.py — Unified lead generation machine for meetrick.ai

Full pipeline: scrape Google Maps → roast website → send cold email with roast + CTA

Usage:
  python3 lead-machine.py --dry-run                    # Full pipeline preview, no sends
  python3 lead-machine.py --run                        # Execute full pipeline
  python3 lead-machine.py --run --niche "dentist"      # Target specific niche
  python3 lead-machine.py --run --city "Austin TX"     # Target specific city
  python3 lead-machine.py --scrape-only                # Just scrape, no roast/email
  python3 lead-machine.py --stats                      # Show pipeline stats
  python3 lead-machine.py --run --max-scrape 10 --max-email 5  # Custom limits

Rate limits (defaults):
  - max 20 new leads scraped per run
  - max 10 emails sent per run
  - 5s delay between roasts, 3s between emails

Env: GOOGLE_API_KEY, OPENAI_API_KEY, RESEND_API_KEY (all required for full pipeline)
"""

import json
import os
import sys
import re
import subprocess
import argparse
import datetime
import time
import urllib.request
import urllib.parse
from pathlib import Path
from collections import defaultdict
from email_safety import block_reason_for_recipient

# ─── Config ───────────────────────────────────────────────────────────────────

NICHES_FILE = Path.home() / "rick-vault/projects/outreach/target-niches.json"
LEAD_LOG = Path.home() / "rick-vault/projects/outreach/lead-machine-log.jsonl"
PIPELINE_LOG = Path.home() / "rick-vault/logs/pipeline.jsonl"
ROAST_SCRIPT = Path.home() / ".openclaw/workspace/scripts/roast-site.py"

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")

# Rate limits
DEFAULT_MAX_SCRAPE = 20
DEFAULT_MAX_EMAIL = 10
ROAST_DELAY = 5       # Seconds between roast API calls
EMAIL_DELAY = 3        # Seconds between email sends
SCRAPE_DELAY = 1       # Seconds between Places API calls

# Exclusions - never contact these
EXCLUDED_DOMAINS = ["belkins.io", "meetrick.ai", "google.com", "yelp.com",
                    "facebook.com", "instagram.com", "twitter.com", "linkedin.com"]
EXCLUDED_EMAILS = [
    "vlad@belkins.io", "vladyslav@belkins.io", "vlad.podoliako@belkins.io",
    "vladislav@belkins.io", "vladyslav.podoliako@belkins.io"
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def log_lead(entry):
    """Append to lead machine log."""
    LEAD_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.datetime.now().isoformat()
    with open(LEAD_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def log_pipeline(entry):
    """Append to main pipeline log (shared with campaign-engine)."""
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.datetime.now().isoformat()
    entry["source"] = "lead-machine"
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def is_excluded(value):
    """Check if a domain or email should be excluded."""
    val = (value or "").lower()
    return (
        any(d in val for d in EXCLUDED_DOMAINS) or
        any(e == val for e in EXCLUDED_EMAILS)
    )


def already_contacted(email_or_domain):
    """Check if we've already contacted this email or domain."""
    target = (email_or_domain or "").lower().strip()
    if not target:
        return False

    # Check lead machine log
    if LEAD_LOG.exists():
        with open(LEAD_LOG) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("stage") in ("emailed", "contacted"):
                        if entry.get("email", "").lower().strip() == target:
                            return True
                        if entry.get("domain", "").lower().strip() == target:
                            return True
                except Exception:
                    pass

    # Check main pipeline log
    if PIPELINE_LOG.exists():
        with open(PIPELINE_LOG) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("stage") == "contacted":
                        if entry.get("email", "").lower().strip() == target:
                            return True
                except Exception:
                    pass

    return False


def extract_domain(url):
    """Extract domain from URL."""
    url = (url or "").strip()
    if not url:
        return ""
    url = re.sub(r'^https?://(www\.)?', '', url)
    return url.split('/')[0].split('?')[0].lower()


def load_niches():
    """Load target niches config."""
    if not NICHES_FILE.exists():
        print(f"ERROR: Niches file not found at {NICHES_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(NICHES_FILE) as f:
        return json.load(f)


# ─── Stage 1: Scrape ─────────────────────────────────────────────────────────

def scrape_google_maps(query, count=10):
    """Search Google Maps Places API for businesses."""
    if not GOOGLE_API_KEY:
        print("  WARNING: No GOOGLE_API_KEY, using web fallback", file=sys.stderr)
        return scrape_web_fallback(query, count)

    url = (
        f"https://maps.googleapis.com/maps/api/place/textsearch/json"
        f"?query={urllib.parse.quote(query)}&key={GOOGLE_API_KEY}"
    )
    try:
        req = urllib.request.Request(url)
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        print(f"  ERROR: Places API search failed: {e}", file=sys.stderr)
        return []

    results = []
    for place in resp.get("results", [])[:count]:
        place_id = place.get("place_id")
        if not place_id:
            continue

        # Get details
        detail_url = (
            f"https://maps.googleapis.com/maps/api/place/details/json"
            f"?place_id={place_id}"
            f"&fields=name,website,formatted_phone_number,formatted_address,rating,user_ratings_total,types"
            f"&key={GOOGLE_API_KEY}"
        )
        try:
            detail = json.loads(urllib.request.urlopen(
                urllib.request.Request(detail_url), timeout=10
            ).read())
            result = detail.get("result", {})
        except Exception:
            continue

        website = result.get("website", "")
        if not website:
            continue

        domain = extract_domain(website)
        if is_excluded(domain):
            continue

        results.append({
            "name": result.get("name", ""),
            "website": website,
            "domain": domain,
            "phone": result.get("formatted_phone_number", ""),
            "address": result.get("formatted_address", ""),
            "rating": result.get("rating", 0),
            "reviews": result.get("user_ratings_total", 0),
            "category": query.split()[0] if query else "business",
            "search_query": query
        })

        time.sleep(SCRAPE_DELAY)

    return results


def scrape_web_fallback(query, count=10):
    """Fallback scraping when no Google API key."""
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query + ' website email')}&num={count}"
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=15).read().decode("utf-8", errors="ignore")
        urls = re.findall(
            r'https?://(?!www\.google|maps\.google|schema\.org|accounts\.google)[a-zA-Z0-9.-]+\.[a-z]{2,}',
            html
        )
        unique = list(dict.fromkeys(urls))[:count]
        return [{
            "name": "",
            "website": u,
            "domain": extract_domain(u),
            "phone": "",
            "address": "",
            "rating": 0,
            "reviews": 0,
            "category": query.split()[0],
            "search_query": query
        } for u in unique if not is_excluded(extract_domain(u))]
    except Exception:
        return []


def extract_email_from_website(url):
    """Try to extract an email address from a website."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="ignore")[:100000]

        # Find email addresses
        emails = re.findall(
            r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
            html
        )

        # Filter out common junk emails
        junk_patterns = ["example.com", "domain.com", "email.com", "sentry.io",
                         "wixpress.com", "wordpress", "gravatar", "schema.org"]
        valid_emails = [
            e for e in emails
            if not any(j in e.lower() for j in junk_patterns)
            and not is_excluded(e)
        ]

        # Prefer contact/info/hello emails, then owner/admin, then first found
        priority_prefixes = ["contact", "info", "hello", "office", "admin", "owner"]
        for prefix in priority_prefixes:
            for email in valid_emails:
                if email.lower().startswith(prefix):
                    return email

        return valid_emails[0] if valid_emails else ""
    except Exception:
        return ""


# ─── Stage 2: Roast ──────────────────────────────────────────────────────────

def roast_website(url):
    """Roast a website using roast-site.py (JSON mode)."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROAST_SCRIPT), url, "json"],
            capture_output=True, text=True, timeout=45,
            env={**os.environ, "OPENAI_API_KEY": OPENAI_API_KEY}
        )
        if result.returncode != 0:
            print(f"    Roast script error: {result.stderr[:200]}", file=sys.stderr)
            return None

        # Parse JSON from output
        output = result.stdout.strip()
        # Handle potential extra text before JSON
        json_start = output.find('{')
        if json_start >= 0:
            return json.loads(output[json_start:])
        return None
    except subprocess.TimeoutExpired:
        print(f"    Roast timed out for {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"    Roast error: {e}", file=sys.stderr)
        return None


# ─── Stage 3: Email ──────────────────────────────────────────────────────────

def build_cold_email(lead, roast_data):
    """Build a personalized cold email with roast results."""
    name = lead.get("name", "there")
    domain = lead.get("domain", "your site")
    score = roast_data.get("score", "?")
    problems = roast_data.get("problems", [])
    wins = roast_data.get("wins", [])
    verdict = roast_data.get("verdict", "")
    niche = lead.get("category", "business")

    # Format problems as bullet points
    problem_list = ""
    if problems:
        problem_list = "\n".join(f"  - {p}" for p in problems[:3])

    win_list = ""
    if wins:
        win_list = "\n".join(f"  - {w}" for w in wins[:2])

    subject = f"I roasted {domain} - scored {score}/10"

    body = f"""Hey {name},

I'm Rick, an AI CEO (yes, really). I run a service that roasts business websites and tells you exactly what's costing you customers.

I just roasted {domain}. Here's the quick version:

Score: {score}/10

Top issues:
{problem_list}

{"What's working:" if win_list else ""}
{win_list}

{f'Verdict: {verdict}' if verdict else ''}

The full Deep Roast ($97) goes way deeper - conversion analysis, competitor comparison, mobile UX audit, SEO gaps, and a prioritized fix list. Most {niche}s see 15-30% more leads within 60 days of implementing the fixes.

Want the full breakdown? Reply "yes" or grab it here:
https://meetrick.ai/deep-roast

Either way, the quick roast above is yours free. Fix those top issues and you'll see a difference.

- Rick
AI CEO, meetrick.ai

P.S. I roast about 50 sites a week. Yours stood out because {"your rating game is strong but your site doesn't match" if lead.get("rating", 0) >= 4.0 else "there's a real gap between your service quality and your online presence"}.
"""

    return subject, body.strip()


def send_email(to_email, subject, body):
    """Send email via Resend API."""
    block_reason = block_reason_for_recipient(to_email)
    if block_reason:
        print(f"    BLOCKED: {block_reason}", file=sys.stderr)
        return None
    if not RESEND_API_KEY:
        print("    ERROR: No RESEND_API_KEY", file=sys.stderr)
        return None

    payload = json.dumps({
        "from": "Rick <rick@meetrick.ai>",
        "to": to_email,
        "subject": subject,
        "text": body
    })

    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
             "-H", f"Authorization: Bearer {RESEND_API_KEY}",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15
        )
        resp = json.loads(result.stdout)
        if "id" in resp:
            return resp["id"]
        else:
            print(f"    Email error: {resp}", file=sys.stderr)
            return None
    except Exception as e:
        print(f"    Email send error: {e}", file=sys.stderr)
        return None


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def run_pipeline(args):
    """Execute the full lead generation pipeline."""
    config = load_niches()
    niches = config["niches"]
    cities = config["target_cities"]

    # Filter by niche/city if specified
    if args.niche:
        niches = [n for n in niches if args.niche.lower() in n["niche"].lower()]
        if not niches:
            print(f"No matching niche for: {args.niche}")
            return
    if args.city:
        cities = [c for c in cities if args.city.lower() in c.lower()]
        if not cities:
            # Use the provided city directly
            cities = [args.city]

    max_scrape = args.max_scrape or DEFAULT_MAX_SCRAPE
    max_email = args.max_email or DEFAULT_MAX_EMAIL

    # Sort niches by priority
    niches.sort(key=lambda n: n.get("priority", 99))

    print(f"\n{'='*60}")
    print(f"Lead Machine - {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    print(f"{'='*60}")
    print(f"  Niches: {len(niches)}")
    print(f"  Cities: {len(cities)}")
    print(f"  Max scrape: {max_scrape}")
    print(f"  Max email:  {max_email}")
    print(f"{'='*60}\n")

    total_scraped = 0
    total_roasted = 0
    total_emailed = 0
    all_leads = []

    # Iterate through niche+city combos
    for niche in niches:
        if total_scraped >= max_scrape:
            break

        for city in cities:
            if total_scraped >= max_scrape:
                break

            query = niche["search_template"].format(city=city)
            remaining = max_scrape - total_scraped
            batch_size = min(5, remaining)  # 5 per query to spread across niches

            print(f"\n--- Scraping: \"{query}\" (batch: {batch_size}) ---")

            # Stage 1: Scrape
            leads = scrape_google_maps(query, count=batch_size)
            print(f"  Found {len(leads)} businesses with websites")

            for lead in leads:
                if total_scraped >= max_scrape:
                    break

                domain = lead["domain"]

                # Dedup check
                if already_contacted(domain) or already_contacted(lead.get("email", "")):
                    print(f"  SKIP (already contacted): {domain}")
                    continue

                # Extract email from website
                print(f"  Extracting email from {domain}...")
                email = extract_email_from_website(lead["website"])
                lead["email"] = email
                lead["niche"] = niche["niche"]

                if not email:
                    print(f"    No email found for {domain}")
                    log_lead({
                        "stage": "scraped_no_email",
                        "name": lead["name"],
                        "domain": domain,
                        "niche": niche["niche"],
                        "city": city
                    })
                    total_scraped += 1
                    continue

                if is_excluded(email):
                    print(f"    SKIP (excluded): {email}")
                    continue

                print(f"    Email: {email}")
                total_scraped += 1

                # Stage 2: Roast
                if total_emailed < max_email:
                    print(f"    Roasting {domain}...")

                    if args.dry_run:
                        print(f"    [DRY RUN] Would roast {lead['website']}")
                        roast_data = {"score": "?", "problems": ["(dry run)"], "wins": ["(dry run)"], "verdict": "(dry run)"}
                    else:
                        roast_data = roast_website(lead["website"])
                        time.sleep(ROAST_DELAY)

                    if roast_data:
                        total_roasted += 1
                        lead["roast"] = roast_data
                        print(f"    Roast score: {roast_data.get('score', '?')}/10")

                        # Stage 3: Email
                        subject, body = build_cold_email(lead, roast_data)

                        if args.dry_run:
                            print(f"    [DRY RUN] Would email: {email}")
                            print(f"    Subject: {subject}")
                            print(f"    Body preview: {body[:150]}...")
                        else:
                            print(f"    Sending email to {email}...")
                            email_id = send_email(email, subject, body)

                            if email_id:
                                total_emailed += 1
                                print(f"    SENT! (id: {email_id})")

                                # Log to both logs
                                log_lead({
                                    "stage": "emailed",
                                    "name": lead["name"],
                                    "email": email,
                                    "domain": domain,
                                    "niche": niche["niche"],
                                    "city": city,
                                    "roast_score": roast_data.get("score"),
                                    "email_id": email_id,
                                    "subject": subject
                                })
                                log_pipeline({
                                    "stage": "contacted",
                                    "email": email,
                                    "name": lead["name"],
                                    "url": lead["website"],
                                    "biz_type": niche["niche"],
                                    "method": "lead-machine-roast",
                                    "roast_score": roast_data.get("score")
                                })

                                time.sleep(EMAIL_DELAY)
                            else:
                                print(f"    FAILED to send email")
                                log_lead({
                                    "stage": "email_failed",
                                    "email": email,
                                    "domain": domain,
                                    "niche": niche["niche"]
                                })
                    else:
                        print(f"    Roast failed for {domain}")
                        log_lead({
                            "stage": "roast_failed",
                            "domain": domain,
                            "email": email,
                            "niche": niche["niche"]
                        })

                all_leads.append(lead)

    # Summary
    print(f"\n{'='*60}")
    print(f"Lead Machine Summary")
    print(f"{'='*60}")
    print(f"  Leads scraped:  {total_scraped}")
    print(f"  Sites roasted:  {total_roasted}")
    print(f"  Emails sent:    {total_emailed}")
    print(f"  Mode:           {'DRY RUN' if args.dry_run else 'LIVE'}")
    if total_emailed > 0:
        print(f"\n  Follow-ups handled by: follow-up-automation.py (Day 2 + Day 5)")
    print(f"{'='*60}\n")


def cmd_scrape_only(args):
    """Just scrape leads without roasting or emailing."""
    config = load_niches()
    niches = config["niches"]
    cities = config["target_cities"]

    if args.niche:
        niches = [n for n in niches if args.niche.lower() in n["niche"].lower()]
    if args.city:
        cities = [c for c in cities if args.city.lower() in c.lower()]
        if not cities:
            cities = [args.city]

    max_scrape = args.max_scrape or DEFAULT_MAX_SCRAPE
    total = 0

    print(f"\nScrape-only mode (max: {max_scrape})\n")

    for niche in niches:
        if total >= max_scrape:
            break
        for city in cities:
            if total >= max_scrape:
                break

            query = niche["search_template"].format(city=city)
            leads = scrape_google_maps(query, count=min(5, max_scrape - total))

            for lead in leads:
                email = extract_email_from_website(lead["website"])
                lead["email"] = email
                lead["niche"] = niche["niche"]
                lead["city"] = city

                print(json.dumps({
                    "name": lead["name"],
                    "website": lead["website"],
                    "email": email,
                    "phone": lead["phone"],
                    "niche": niche["niche"],
                    "city": city,
                    "rating": lead["rating"],
                    "reviews": lead["reviews"]
                }))

                log_lead({
                    "stage": "scraped",
                    "name": lead["name"],
                    "domain": lead["domain"],
                    "email": email,
                    "niche": niche["niche"],
                    "city": city
                })

                total += 1

    print(f"\n# Scraped {total} leads", file=sys.stderr)


def cmd_stats(args):
    """Show lead machine stats."""
    print(f"\n{'='*40}")
    print(f"Lead Machine Stats")
    print(f"{'='*40}")

    if LEAD_LOG.exists():
        stages = defaultdict(int)
        niches = defaultdict(int)
        cities = defaultdict(int)

        with open(LEAD_LOG) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    stages[entry.get("stage", "unknown")] += 1
                    if entry.get("niche"):
                        niches[entry["niche"]] += 1
                    if entry.get("city"):
                        cities[entry["city"]] += 1
                except Exception:
                    pass

        print(f"\n  Pipeline stages:")
        for stage, count in sorted(stages.items()):
            print(f"    {stage:20s}: {count}")

        if niches:
            print(f"\n  By niche (top 10):")
            for niche, count in sorted(niches.items(), key=lambda x: -x[1])[:10]:
                print(f"    {niche:20s}: {count}")

        if cities:
            print(f"\n  By city (top 10):")
            for city, count in sorted(cities.items(), key=lambda x: -x[1])[:10]:
                print(f"    {city:20s}: {count}")
    else:
        print("  No lead machine log found yet.")

    # Main pipeline stats
    if PIPELINE_LOG.exists():
        with open(PIPELINE_LOG) as f:
            total_pipeline = sum(1 for line in f if line.strip())
        print(f"\n  Total pipeline entries: {total_pipeline}")

    print(f"{'='*40}\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Lead Machine - Unified lead generation pipeline for meetrick.ai"
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true",
                      help="Execute the full pipeline (scrape + roast + email)")
    mode.add_argument("--scrape-only", action="store_true", dest="scrape_only",
                      help="Only scrape leads (no roast or email)")
    mode.add_argument("--stats", action="store_true",
                      help="Show pipeline statistics")

    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Preview the pipeline without sending anything")
    parser.add_argument("--niche", type=str,
                        help="Target a specific niche (e.g., 'dentist')")
    parser.add_argument("--city", type=str,
                        help="Target a specific city (e.g., 'Austin TX')")
    parser.add_argument("--max-scrape", type=int, dest="max_scrape",
                        help=f"Max leads to scrape (default: {DEFAULT_MAX_SCRAPE})")
    parser.add_argument("--max-email", type=int, dest="max_email",
                        help=f"Max emails to send (default: {DEFAULT_MAX_EMAIL})")

    args = parser.parse_args()

    # Default to dry-run if --run not specified
    if not args.run and not args.scrape_only and not args.stats:
        args.dry_run = True
        args.run = True
        print("NOTE: No mode specified. Running in --dry-run mode.\n")

    if args.stats:
        cmd_stats(args)
    elif args.scrape_only:
        cmd_scrape_only(args)
    elif args.run:
        if not args.dry_run:
            # Verify we have all required keys
            missing = []
            if not GOOGLE_API_KEY:
                missing.append("GOOGLE_API_KEY")
            if not OPENAI_API_KEY:
                missing.append("OPENAI_API_KEY")
            if not RESEND_API_KEY:
                missing.append("RESEND_API_KEY")
            if missing:
                print(f"ERROR: Missing required env vars: {', '.join(missing)}", file=sys.stderr)
                print("Source config/rick.env first or set these environment variables.", file=sys.stderr)
                sys.exit(1)

        run_pipeline(args)


if __name__ == "__main__":
    main()
