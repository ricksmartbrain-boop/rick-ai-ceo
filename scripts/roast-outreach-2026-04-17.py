#!/usr/bin/env python3
"""
Roast-based cold outreach run — 2026-04-17
Sends 10 personalized roast emails via Resend + logs results.
"""
import json
import os
import time
import datetime
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path
from email_safety import block_reason_for_recipient


def has_valid_mx(email: str) -> bool:
    """Return True only if the email domain has at least one MX record."""
    try:
        import dns.resolver
        domain = email.split("@")[-1]
        dns.resolver.resolve(domain, "MX")
        return True
    except Exception:
        # Fallback: dig-based check (no dnspython dependency required)
        try:
            domain = email.split("@")[-1]
            result = subprocess.run(
                ["dig", "+short", "MX", domain],
                capture_output=True, text=True, timeout=5
            )
            return bool(result.stdout.strip())
        except Exception:
            return False

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM = "Rick <rick@meetrick.ai>"
DEEP_ROAST_LINK = "https://buy.stripe.com/7sY00j8wL9Dm3lab9f0x20D"
MEETRICK_ROAST = "https://meetrick.ai/roast?utm_source=resend&utm_medium=email&utm_campaign=roast-outreach"
MEETRICK_HOME = "https://meetrick.ai?utm_source=resend&utm_medium=email&utm_campaign=roast-outreach"
LOG_PATH = Path.home() / "rick-vault/experiments/roast-outreach-2026-04-17.md"
CONTACTED_FILE = Path.home() / "rick-vault/projects/outreach/contacted.json"
EMAIL_DELAY = 3

LEADS = [
    {
        "email": "info@swishdental.com",
        "website": "swishsmiles.com",
        "niche": "dental",
        "score": 8,
        "problems": [
            "Homepage is content-heavy and repetitive — key conversion path gets buried",
            "Primary CTA (book appointment / find a studio) is not visually prioritized",
            "Broad promotional copy with limited proof points like outcomes or provider credentials"
        ],
        "wins": [
            "Strong multi-location structure makes it easy to find a nearby studio",
            "Service breadth covers general, cosmetic, restorative, emergency, and specialty care",
            "Multiple conversion options + financing/insurance resources reduce booking friction"
        ],
        "verdict": "Strong multi-location brand, but the homepage could be cleaner and more trust-focused to convert more visitors into booked patients."
    },
    {
        "email": "info@austincitydental.com",
        "website": "austincitydental.com",
        "niche": "dental",
        "score": 6,
        "problems": [
            "Theme/plugin output is leaking into page source — hurts polish and trust",
            "Content is generic SEO copy, not differentiated enough to win comparison shoppers",
            "Relying on broad 'general & cosmetic' terms without strong service-page depth"
        ],
        "wins": [
            "Title targets a clear local intent keyword: dentist + Austin TX",
            "Brand and location are immediately understandable — solid local SEO foundation",
            "Core dental services are covered, attracting a broad patient base"
        ],
        "verdict": "Solid local SEO foundation, but conversion and trust signals need work to turn organic traffic into booked patients."
    },
    {
        "email": "austingeneraldentistry@gmail.com",
        "website": "austingeneraldentistry.com",
        "niche": "dental",
        "score": 7,
        "problems": [
            "Text-heavy homepage with repeated service blocks — hard to scan quickly",
            "Weak urgency and minimal friction-reducing CTAs beyond a phone number",
            "Messaging feels dated and generic versus other local practices"
        ],
        "wins": [
            "Clear service presentation: family dentistry, implants, cosmetic, same-day crowns",
            "Doctor names, local address, and welcoming messaging build real trust",
            "Educational content and visible review links reinforce credibility"
        ],
        "verdict": "Solid local dental homepage with good trust basics, but sharper messaging and stronger CTAs would turn more visitors into booked patients."
    },
    {
        "email": "appt@austindental.com",
        "website": "austindental.com",
        "niche": "dental",
        "score": 7,
        "problems": [
            "Page looks cluttered and repetitive — navigation, promos, and testimonials compete for attention",
            "Visible raw/garbled HTML content on the page hurts polish and trust",
            "Appointment request and patient portal are present but not visually prioritized"
        ],
        "wins": [
            "Strong local relevance with Austin/78759 positioning and a prominent phone number",
            "Patient testimonials and review-focused messaging build solid trust",
            "Good service breadth: general, cosmetic, emergency, and implant dentistry"
        ],
        "verdict": "Credible local dental site with solid trust signals, but cleanup and sharper conversion focus would meaningfully increase appointment requests."
    },
    {
        "email": "completewellness1777@gmail.com",
        "website": "completewellnesschiro.com",
        "niche": "chiropractic",
        "score": 5,
        "problems": [
            "Homepage is text-heavy and cluttered — main offer and CTAs are hard to scan",
            "Browser/JS warning text visible on-page — looks dated and erodes trust",
            "Generic service descriptions with repeated CTAs and no strong differentiation"
        ],
        "wins": [
            "Clear local positioning for Denver/Glendale/Cherry Creek area",
            "Strong service breadth with condition-based entry points for self-identification",
            "Prominent phone, free consultation offer, and appointment prompts support lead gen"
        ],
        "verdict": "Solid local practice website with decent conversion basics, but the dated presentation is likely costing appointments every week."
    },
    {
        "email": "info@casanovabeauty.com",
        "website": "casanovabeauty.com",
        "niche": "beauty salon",
        "score": 5,
        "problems": [
            "Page content appears polluted with raw CSS text — looks broken and unpolished",
            "No clear conversion path visible — visitors can't quickly find booking or pricing",
            "Weak trust signals and CTA clarity in the above-the-fold content"
        ],
        "wins": [
            "Business is clearly positioned as a natural hair salon in Miami — great for local search",
            "Brand/domain is memorable and directly aligned with beauty services",
            "Location-specific landing page is a strong foundation for local SEO"
        ],
        "verdict": "Promising local beauty brand, but the technical presentation is working against you — cleaning it up could meaningfully lift bookings."
    },
    {
        "email": "hello@larealproperty.com",
        "website": "larealproperty.com",
        "niche": "real estate",
        "score": 6,
        "problems": [
            "Homepage content cluttered by injected CSS artifacts — hurts trust and readability",
            "Hero section is visually heavy and may slow perceived load performance",
            "Core value proposition is not immediately surfaced — weak above-the-fold clarity"
        ],
        "wins": [
            "Strong local-market positioning for Los Angeles real estate is clear",
            "High-end visual presentation works well for property marketing",
            "Search-focused hero indicates intent to help users find homes quickly"
        ],
        "verdict": "Polished market branding, but the page is currently overdesigned and under-optimized for the clarity and conversions you actually need."
    },
    {
        "email": "info@drbaumrind.com",
        "website": "drbaumrind.com",
        "niche": "dental",
        "score": 5,
        "problems": [
            "Page content contaminated with raw CSS/code text — looks broken to first-time visitors",
            "Key conversion info not immediately clear — services, trust signals, and CTAs are buried",
            "Fixed top bar and dense header styling likely hurts mobile usability"
        ],
        "wins": [
            "Brand is clear and local: Baumrind Family Dentistry with an Atlanta identity",
            "Phone and social icons show intent to make contact easy",
            "Responsive approach with modern media queries as a solid technical foundation"
        ],
        "verdict": "Promising local dental brand undermined by visible CSS/code issues and unclear homepage messaging — fixable, and worth fixing."
    },
    {
        "email": "info@smiles4grantpark.com",
        "website": "smiles4grantpark.com",
        "niche": "dental",
        "score": 4,
        "problems": [
            "Page is mostly raw CSS/theme output — terrible content-to-code ratio, weak information hierarchy",
            "No clear conversion path visible — visitors can't find a primary CTA quickly",
            "No trust signals or service-specific messaging evident above the fold"
        ],
        "wins": [
            "Consistent visual system and typography can support a polished brand feel",
            "Domain is highly specific to the local practice — great for local SEO intent",
            "Custom WordPress theme foundation gives flexibility to improve everything"
        ],
        "verdict": "You're probably leaving appointments on the table every single day — the homepage isn't giving visitors a reason to stay or book."
    },
    {
        "email": "smile@craftofdentistry.com",
        "website": "dentistsouthaustintx.com",
        "niche": "dental",
        "score": 4,
        "problems": [
            "Page content appears partially broken — raw Elementor/HTML code visible, hurts trust",
            "Core value proposition is not immediately clear — visitors don't know why to choose you",
            "Possible rendering/implementation issues affecting mobile UX, SEO, and accessibility"
        ],
        "wins": [
            "Clear contact info visible immediately — address and phone for current and new patients",
            "Membership Plan and Book Online CTAs are prominent conversion actions",
            "Locally targeted for Austin — good for nearby search relevance"
        ],
        "verdict": "Strong local intent and solid CTAs, but the broken page rendering is actively sabotaging conversions — it's fixable and worth fixing fast."
    },
]


def build_subject(lead):
    score = lead["score"]
    niche = lead["niche"]
    domain = lead["website"].replace("https://", "").replace("http://", "").rstrip("/")
    
    if score <= 4:
        return f"I roasted {domain} — it scored a {score}/10 (yikes)"
    elif score <= 6:
        return f"Quick roast of {domain} — here's what I found"
    else:
        return f"Your site scored a {score}/10 — here's what's holding it back"


def build_body(lead):
    domain = lead["website"].replace("https://", "").replace("http://", "").rstrip("/")
    score = lead["score"]
    p1, p2, p3 = lead["problems"]
    verdict = lead["verdict"]
    niche = lead["niche"]
    
    # Niche-aware opener
    niche_openers = {
        "dental": "I run an AI that roasts local business websites — brutal, honest, constructive.",
        "chiropractic": "I run an AI that roasts local business websites — and yours caught my eye.",
        "beauty salon": "I run an AI that roasts local business websites — yours was interesting.",
        "real estate": "I run an AI that roasts real estate and local business websites — yours stood out.",
    }
    opener = niche_openers.get(niche, "I run an AI that roasts local business websites — honest, specific, constructive.")
    
    body = f"""Hey,

{opener}

I ran {domain} through it. Here's what came back (score: {score}/10):

❌ {p1}
❌ {p2}
❌ {p3}

Bottom line: {verdict}

The issues above are the kind of thing that silently costs you bookings and leads every week — not because your service isn't great, but because the site isn't converting the traffic you're already getting.

If you want the full teardown — every gap, every fix, prioritized by revenue impact — I offer a Deep Roast for $97:

👉 {DEEP_ROAST_LINK}

Or grab the free version and I'll roast it live:
{MEETRICK_ROAST}

Either way — happy to help.

Rick
rick@meetrick.ai
{MEETRICK_HOME}

P.S. The free roast takes 60 seconds. No pitch, just the truth about your site."""
    
    return body


def send_email(to, subject, body):
    block_reason = block_reason_for_recipient(to)
    if block_reason:
        return {"ok": False, "error": block_reason}
    payload = json.dumps({
        "from": FROM,
        "to": [to],
        "subject": subject,
        "text": body
    })
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", "https://api.resend.com/emails",
                "-H", "Content-Type: application/json",
                "-H", f"Authorization: Bearer {RESEND_API_KEY}",
                "-d", payload,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        data = json.loads(result.stdout or "{}")
        if data.get("id"):
            return {"ok": True, "id": data.get("id", "")}
        return {"ok": False, "error": result.stdout or result.stderr or "unknown resend error"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def update_contacted(email):
    try:
        data = json.loads(CONTACTED_FILE.read_text())
    except:
        data = []
    if isinstance(data, list) and email not in data:
        data.append(email)
        CONTACTED_FILE.write_text(json.dumps(data, indent=2))


def main():
    results = []
    sent_count = 0
    
    print(f"🚀 Roast Outreach Run — {datetime.date.today()}")
    print(f"   Leads queued: {len(LEADS)}")
    print(f"   From: {FROM}")
    print()
    
    for lead in LEADS:
        if sent_count >= 10:
            break
        
        email = lead["email"]
        domain = lead["website"]
        score = lead["score"]
        subject = build_subject(lead)
        body = build_body(lead)
        
        if not has_valid_mx(email):
            print(f"   ⏩ Skipped {email} — no MX record")
            continue

        print(f"📧 Sending to {email} ({domain}, score {score}/10)...")
        print(f"   Subject: {subject}")
        
        result = send_email(email, subject, body)
        
        if result["ok"]:
            print(f"   ✅ Sent! ID: {result['id']}")
            update_contacted(email)
            sent_count += 1
        else:
            print(f"   ❌ Failed: {result['error']}")
        
        results.append({
            "email": email,
            "website": domain,
            "score": score,
            "subject": subject,
            "result": result,
            "timestamp": datetime.datetime.now().isoformat()
        })
        
        if sent_count < 10:
            time.sleep(EMAIL_DELAY)
    
    # Write log
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    successful = [r for r in results if r["result"].get("ok")]
    failed = [r for r in results if not r["result"].get("ok")]
    
    log_content = f"""# Roast Outreach Run — 2026-04-17

## Summary
- **Date:** {datetime.date.today()}
- **Leads targeted:** {len(LEADS)}
- **Emails sent:** {len(successful)}
- **Failed:** {len(failed)}
- **Product:** Deep Roast ($97) — {DEEP_ROAST_LINK}
- **UTM:** utm_source=resend&utm_medium=email&utm_campaign=roast-outreach

## Results

### ✅ Sent ({len(successful)})

| Email | Website | Score | Resend ID |
|-------|---------|-------|-----------|
"""
    for r in successful:
        log_content += f"| {r['email']} | {r['website']} | {r['score']}/10 | {r['result'].get('id','')} |\n"
    
    if failed:
        log_content += f"\n### ❌ Failed ({len(failed)})\n\n"
        for r in failed:
            log_content += f"- {r['email']} ({r['website']}): {r['result'].get('error','')}\n"
    
    log_content += f"""
## Email Template Used

**Subject variants by score:**
- ≤4: "I roasted [domain] — it scored a [X]/10 (yikes)"  
- ≤6: "Quick roast of [domain] — here's what I found"
- >6: "Your site scored a [X]/10 — here's what's holding it back"

**Body:** Opener + 3 specific problems + verdict + Deep Roast CTA ($97) + free roast link

## Notes

- GOOGLE_API_KEY was empty — used existing 3,674 pipeline leads
- Pulled leads with website + email, not yet in contacted.json
- Focused on dental niches (Austin TX, Atlanta GA) + chiropractic + beauty + real estate
- Roasts generated via roast-site.py (OpenAI GPT-4 mini)
- From: {FROM}
"""
    
    LOG_PATH.write_text(log_content)
    print(f"\n📄 Log written to {LOG_PATH}")
    print(f"\n🏁 Done. {len(successful)}/{len(LEADS)} emails sent.")
    
    return results


if __name__ == "__main__":
    main()
