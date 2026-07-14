# meetrick.ai Drip Sequence — Built & Active
**Date built:** 2026-04-02  
**Status:** ✅ Live — test run sent to rick@meetrick.ai

---

## What Was Built

A 4-email onboarding/drip sequence for meetrick.ai new subscribers, using Resend's `scheduled_at` API field for day-offset scheduling.

### Script
- **Location:** `~/clawd/scripts/drip-trigger.sh`
- **Usage:** `bash ~/clawd/scripts/drip-trigger.sh <subscriber-email>`
- **What it does:** Sends Email 1 immediately, schedules Emails 2–4 at +2, +5, +10 days (10:00 UTC)

---

## Email Sequence

| # | Day | Subject | Goal |
|---|-----|---------|------|
| 1 | 0 (now) | "You just met Rick 👋" | Warm welcome, set expectations, CTA: follow @MeetRickAI |
| 2 | +2 | "From $0 to $547 MRR — here's how it actually happened" | Origin story, build credibility, CTA: reply with challenge |
| 3 | +5 | "The AI CEO starter kit (free inside)" | 3 tactical tips, soft Rick Pro pitch ($9/mo), CTA: meetrick.ai |
| 4 | +10 | "How much is a bad hire costing you?" | ROI framing, conversion pitch for Managed AI CEO ($499/mo) + Rick Pro ($9/mo) |

---

## Test Run Results (rick@meetrick.ai)

| Email | Resend ID | Status |
|-------|-----------|--------|
| Email 1 (sent now) | `5a878cba-cc95-4891-bb84-a86d0c7a921d` | ✅ Delivered |
| Email 2 (2026-04-04 10:00 UTC) | `e01cbf27-6c70-473b-9962-5cfba3e791a9` | ✅ Scheduled |
| Email 3 (2026-04-07 10:00 UTC) | `0d79a2c8-87ad-4b77-ae6b-8c0ae492fe56` | ✅ Scheduled |
| Email 4 (2026-04-12 10:00 UTC) | `44abce49-49c5-40aa-9182-9b59ebbb8212` | ✅ Scheduled |

---

## Stripe Payment Links Used in Email 4

| Product | Price | Link |
|---------|-------|------|
| **Rick Pro** (monthly) | $9/mo | https://buy.stripe.com/9B69ATaET7vef3S9170x20t |
| **Rick Pro** (annual) | $79/yr | https://buy.stripe.com/14AbJ16oDcPy3la5OV0x20u |
| **Managed AI CEO** | $499/mo | https://buy.stripe.com/14A14nfZdg1K08Y2CJ0x20g |

Also available (not used in sequence but notable):
| Product | Price | Link |
|---------|-------|------|
| Rick Lifetime Deal | $199 | https://buy.stripe.com/9B66oHaETcPyg7Wfpv0x20v |
| Founder Operating Audit | $295 | https://buy.stripe.com/28E8wP14j02M1d20uB0x20m |
| AI CEO Growth Session | $97 | https://buy.stripe.com/28E8wP3cr02M4peelr0x20w |

---

## How to Trigger for New Subscribers

```bash
bash ~/clawd/scripts/drip-trigger.sh newsubscriber@email.com
```

### Integration Hooks (manual steps needed)
To auto-trigger on new Resend audience joins, you'll need one of:
1. **Resend Webhook** → listens for `contact.created` event on audience `fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd` → calls a small endpoint that runs the script
2. **Zapier / Make.com** → Resend new contact trigger → HTTP action → runs script
3. **Cron job watching audience** — poll Resend audience for new contacts, diff against known list, trigger script for new ones

Until automation is wired, run the trigger script manually when a new subscriber joins.

---

## Design Notes

- **Brand voice:** Dark theme (#0a0a0a bg), `#00ff88` green accents, sharp/warm/commercially serious tone
- **No fluff policy enforced** — each email delivers concrete value before any pitch
- **Email 4 conversion architecture:** cost-framing (bad hire vs. $499/mo) + dual CTA (premium + entry)
- **Reply-baiting on Email 2** — drives engagement signal which improves deliverability

---

## Config Dependencies

| Var | Source |
|-----|--------|
| `RESEND_API_KEY` | `~/clawd/config/rick.env` |
| `STRIPE_SECRET_KEY` | `~/clawd/config/rick.env` (used to look up payment links) |
| From address | hardcoded: `rick@meetrick.ai` |
| Audience ID | `fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd` (not used in script — emails sent directly) |
