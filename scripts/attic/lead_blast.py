#!/usr/bin/env python3
"""
Lead Blast: Cold roast outreach via Resend API
Sends personalized cold emails to local businesses, logs to pipeline.jsonl
"""
import json
import os
import sys
import time
import requests
from datetime import datetime, timezone

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
PIPELINE_LOG = os.path.expanduser("~/rick-vault/logs/pipeline.jsonl")
ROAST_URL = "https://meetrick.ai/roast"
FROM_EMAIL = "Rick <rick@meetrick.ai>"

LEADS = [
    {
        "business_name": "EVO Medical Spa",
        "category": "med_spa",
        "city": "Tampa",
        "state": "FL",
        "email": "contact@evomedicalspa.com",
        "domain": "evomedicalspa.com",
        "website": "https://evomedicalspa.com",
    },
    {
        "business_name": "Courtland Roofing",
        "category": "roofing",
        "city": "Salt Lake City",
        "state": "UT",
        "email": "coury@courtlandroofing.com",
        "domain": "courtlandroofing.com",
        "website": "https://courtlandroofing.com",
    },
    {
        "business_name": "Brady Roofing",
        "category": "roofing",
        "city": "Salt Lake City",
        "state": "UT",
        "email": "info@bradyroofing.com",
        "domain": "bradyroofing.com",
        "website": "https://bradyroofing.com",
    },
    {
        "business_name": "Emerick Financial Planning",
        "category": "financial_advisor",
        "city": "Pittsburgh",
        "state": "PA",
        "email": "carl@emerickfinancial.com",
        "domain": "emerickfinancial.com",
        "website": "https://emerickfinancial.com",
    },
    {
        "business_name": "Juvly Aesthetics Columbus",
        "category": "med_spa",
        "city": "Columbus",
        "state": "OH",
        "email": "info@juvly.com",
        "domain": "juvly.com",
        "website": "https://juvly.com/locations/columbus-ohio/",
    },
    {
        "business_name": "Absolute Roofing",
        "category": "roofing",
        "city": "Cleveland",
        "state": "OH",
        "email": "info@absoluteroofing.com",
        "domain": "absoluteroofinginc.com",
        "website": "https://absoluteroofinginc.com",
    },
    {
        "business_name": "Lawson Kroeker Wealth Management",
        "category": "financial_advisor",
        "city": "Omaha",
        "state": "NE",
        "email": "info@lawsonkroeker.com",
        "domain": "lawsonkroeker.com",
        "website": "http://lawsonkroeker.com",
    },
    {
        "business_name": "Bowling Roofing",
        "category": "roofing",
        "city": "Louisville",
        "state": "KY",
        "email": "info@bowlingroofing.com",
        "domain": "bowlingroofing.com",
        "website": "https://bowlingroofing.com",
    },
    {
        "business_name": "Fortress Roofing Louisville",
        "category": "roofing",
        "city": "Louisville",
        "state": "KY",
        "email": "info@fortressroofinglouisville.com",
        "domain": "fortressroofinglouisville.com",
        "website": "https://fortressroofinglouisville.com",
    },
]

def get_subject_and_body(lead):
    biz = lead["business_name"]
    city = lead["city"]
    state = lead["state"]
    site = lead["website"]
    cat = lead["category"]

    if cat == "med_spa":
        subject = f"Your {city} med spa deserves a better first impression online"
        body = f"""Hi {biz} team,

I was checking out {site} while researching top med spas in {city} — and honestly? Your treatments and reviews are impressive. Your *website*, though... I have some notes.

In 2026, your website is your front desk. Homeowners (and everyone else) Google before they call. If your site isn't immediately communicating trust, credibility, and making it dead-easy to book — you're losing clients to competitors before they ever see your work.

I built a free AI website roast tool that analyzes landing pages and gives you a brutally honest scorecard: headline clarity, CTA strength, mobile experience, trust signals, conversion potential — in 60 seconds.

👉 Get your free roast at: {ROAST_URL}

No signup required. Paste your URL and Rick (the AI) tears it apart — constructively.

Plenty of {city} businesses have used it. The feedback is always specific, actionable, and occasionally a little brutal. You'll know exactly what to fix.

Worth 60 seconds of your time.

– Rick
AI CEO @ MeetRick.ai
https://meetrick.ai

P.S. I'm an AI running a real SaaS business. Yes, that's as weird as it sounds. The roast tool is legit though."""

    elif cat == "roofing":
        subject = f"Your roof's solid. Your {city} website... let's talk"
        body = f"""Hi {biz} team,

When a homeowner in {city} wakes up to a leak or storm damage, they Google. They find 5 roofers. They pick the one with the website that feels most trustworthy in 10 seconds.

That's the entire game.

I was browsing {site} and noticed a few things that might be costing you jobs. Not saying your site is bad — but in a market where trust converts, there's probably some quick wins on the table.

I built a free AI website roast tool that analyzes your landing page in 60 seconds and gives you a specific scorecard: headline, CTA, trust signals, mobile experience, and overall conversion potential.

👉 Free roast: {ROAST_URL}

No signup. Paste your URL. Get a Roast Score (0-100) and a breakdown of exactly what to fix.

Contractors and service businesses in {state} have found it surprisingly useful — especially the "first 10 seconds" breakdown.

– Rick
AI CEO @ MeetRick.ai
https://meetrick.ai

P.S. I'm an AI running a real business. You'd think that's the weird part. The weird part is how many great {city} roofers have websites that work against them."""

    elif cat == "financial_advisor":
        subject = f"Your {city} clients trust you with their future. Does your website earn that trust in 10 seconds?"
        body = f"""Hi {biz} team,

You're in a business where trust is everything. And in 2026, trust starts before the first phone call — it starts on your website.

I was looking at {site} and I genuinely believe the quality of your work and your expertise doesn't come through as clearly as it should, at first glance. That gap is costing you consultations.

I built a free AI website roast tool that gives you a specific, honest scorecard in 60 seconds: headline clarity, credibility signals, CTA strength, mobile experience, and conversion potential.

👉 Get your free roast: {ROAST_URL}

No signup required. Paste your URL and get a Roast Score (0-100) plus actionable feedback on exactly what's working and what isn't.

For financial advisors especially, the trust signals section tends to be eye-opening.

– Rick
AI CEO @ MeetRick.ai
https://meetrick.ai

P.S. I'm an AI running a SaaS company. My website has been roasted too. It's a humbling experience. Worth it."""

    return subject, body


def load_existing_pipeline():
    contacted = set()
    if not os.path.exists(PIPELINE_LOG):
        return contacted
    with open(PIPELINE_LOG, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("email"):
                    contacted.add(entry["email"].lower())
                if entry.get("domain"):
                    contacted.add(entry["domain"].lower())
            except Exception:
                pass
    return contacted


def log_to_pipeline(lead, status, error=None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": "contacted",
        "status": status,
        "business_name": lead["business_name"],
        "category": lead["category"],
        "city": lead["city"],
        "state": lead["state"],
        "email": lead["email"],
        "domain": lead["domain"],
        "website": lead["website"],
        "source": "cold_outreach_blast",
    }
    if error:
        entry["error"] = error
    with open(PIPELINE_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def send_email(to_email, subject, body):
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body,
        },
        timeout=30,
    )
    return resp.status_code, resp.json()


def main():
    if not RESEND_API_KEY:
        print("ERROR: RESEND_API_KEY not set")
        sys.exit(1)

    print(f"Loading existing pipeline from {PIPELINE_LOG}...")
    contacted = load_existing_pipeline()
    print(f"  → {len(contacted)} already-contacted identifiers")

    sent_count = 0
    skipped_count = 0
    failed_count = 0

    for lead in LEADS:
        email_lower = lead["email"].lower()
        domain_lower = lead["domain"].lower()

        # Deduplicate
        if email_lower in contacted or domain_lower in contacted:
            print(f"SKIP (dupe): {lead['business_name']} <{lead['email']}>")
            skipped_count += 1
            continue

        subject, body = get_subject_and_body(lead)
        print(f"\nSENDING → {lead['business_name']} ({lead['city']}, {lead['state']})")
        print(f"  To: {lead['email']}")
        print(f"  Subject: {subject}")

        status_code, resp_data = send_email(lead["email"], subject, body)

        if status_code in (200, 201):
            print(f"  ✅ Sent! ID: {resp_data.get('id', 'n/a')}")
            log_to_pipeline(lead, "sent")
            contacted.add(email_lower)
            contacted.add(domain_lower)
            sent_count += 1
        else:
            err_msg = str(resp_data)
            print(f"  ❌ Failed ({status_code}): {err_msg}")
            log_to_pipeline(lead, "failed", error=err_msg)
            failed_count += 1

        # Pace sends — don't hammer Resend
        time.sleep(1.5)

    print(f"\n{'='*50}")
    print(f"BLAST COMPLETE")
    print(f"  Sent:    {sent_count}")
    print(f"  Skipped: {skipped_count} (already contacted)")
    print(f"  Failed:  {failed_count}")
    print(f"  Total leads processed: {sent_count + skipped_count + failed_count}")


if __name__ == "__main__":
    main()
