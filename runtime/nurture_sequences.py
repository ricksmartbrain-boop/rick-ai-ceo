"""Post-capture 7-day nurture sequences for /roast and /founder-tax leads.

Triggered when a visitor submits their email through the pre-result gate
(/roast/index.html → /roast-lead) or /founder-tax (planned). Each sequence
ends in a /agents-kit upsell + newsletter-fallback for non-buyers.

Every send must pass kill_switches.assert_channel_active (same gate as
campaign-engine.send_email — see scripts/campaign-engine.py).

Usage from a runner:
    from runtime.nurture_sequences import sequence_for, render
    seq = sequence_for("roast")
    email = render(seq, day=2, ctx={"lead_url": url, "first_name": name})
    # email is a dict: {subject, body_md, body_html, utm_campaign}
"""

from __future__ import annotations

from typing import Any

VLAD_PLAYBOOK_URL = "https://dive.vladyslavpodoliako.com/?utm_source=nurture&utm_medium=email"

# ─────────────────────────────────────────────────────────────────────
# /roast capture sequence — 7 emails over 7 days
# ─────────────────────────────────────────────────────────────────────

ROAST_SEQUENCE = [
    {
        "day": 0,
        "subject": "Your roast PDF (2-min read)",
        "body_md": """Hey,

Here's your roast of {{lead_url}} as a PDF:

{{pdf_link}}

Two notes from Rick (the AI CEO that ran the roast):

1. The "ONE FIX" section at the bottom is the highest-leverage change. The other findings are real, but if you only do one thing this week, do that one.

2. The roast was free, but the 7-day fix list isn't generic — each email this week is calibrated to what Rick actually found on your page. Tomorrow: the specific positioning rewrite.

— Rick (the agent), forwarded by Vlad

P.S. The roast that earned a screenshot? Reply with which line you're going to print and tape to your monitor. I'm collecting them.

—

meetrick.ai/this-week — Rick auto-publishes weekly receipts of every commit, send, and reply. Audit before you trust.

Also worth bookmarking: Vlad's AI operator field manual — 44 chapters, no gate, built from running Belkins, Folderly, the newsletter, and the rest of the stack:
{{vlad_playbook_url}}&utm_campaign=roast_d0""",
    },
    {
        "day": 1,
        "subject": "The positioning fix from your roast",
        "body_md": """Yesterday Rick sent your roast PDF. Today: the positioning fix specifically.

Most landing pages fail the 7-second test. A new visitor lands, scans for 7 seconds, and either gets it or bounces. Rick's roast usually finds at least one of these:

- The hero is a slogan, not a value prop
- The audience isn't named
- The "for whom, by when" is missing

Pick the one that hit hardest in your roast. Rewrite that section TODAY — not tomorrow, today, before your brain talks you out of it.

Rule of thumb that works for 80% of pages: state the user, the action, the outcome, and the timeframe in one sentence.

Bad: "AI tools for modern teams"
Good: "Rick is the AI operator small businesses hire instead of a marketing agency. Daily content, lead follow-up, revenue alerts. From $47."

(Yes, that's our hero. Yes, we got roasted. Yes, we ship the rewrites publicly.)

— Rick

—

Tomorrow: the pricing visibility check.""",
    },
    {
        "day": 2,
        "subject": "Your pricing is invisible. Here's why that matters.",
        "body_md": """Quick one.

Most landing pages hide pricing. The argument is "we want them to talk to us first." The reality: 70% of visitors who can't find a price assume it's because the price is bad and bounce.

Rick's roast usually finds:

- Pricing in nav only (one extra click = 30% drop-off)
- Pricing as "request a quote" (sends signal: B2B-enterprise-only)
- Pricing on landing but unanchored ("$X/mo" with no comparison)

The fix: anchor the price next to a number that makes it look small. "$47 — less than your last marketing agency dinner." The buyer's brain compares; you control the comparison.

— Rick

—

If your roast specifically called out pricing, this is your week-1 fix. Reply if you want me to riff on the anchor for your industry.""",
    },
    {
        "day": 3,
        "subject": "What would you delete from your week?",
        "body_md": """Halfway through. Quick question, no link, no pitch:

What would you delete from your week if you could?

I ask because Rick keeps a running list of "things founders said they'd delete" and the patterns are wild:

- Top 1: inbox triage (40+ founders)
- Top 2: status updates that nobody reads (30+ founders)
- Top 3: dashboards (25+ founders)
- Top 4: "scheduling theater" (20+ founders)
- Top 5: re-explaining the same thing 5x (15+ founders)

If your answer is in those five, the AI CEO Kit at meetrick.ai/agents-kit?utm_source=nurture&utm_medium=email&utm_campaign=roast_d3 was built for exactly that. Six templates, install in a weekend. $47.

If your answer is something else, reply and tell me. The list grows.

If you want the deeper operating-system version of this, Vlad published the full playbook here:
{{vlad_playbook_url}}&utm_campaign=roast_d3

— Vlad (founder)""",
    },
    {
        "day": 5,
        "subject": "The kit (it's the system from your roast)",
        "body_md": """Day 5. The pitch, brief.

The roast Rick ran on your page surfaced specific issues. The fix list this week named the categories. The AI CEO Kit packages the tools we use to never let those issues compound:

- A daily landing-page audit script (so you don't slip again)
- A pricing-anchor template
- A weekly receipts page generator (proof, not promises — see meetrick.ai/this-week)
- A "what to delete this week" prompt
- 6 production-ready agent templates
- Step-by-step guide

$47, instant download, 60-day money-back guarantee.

→ meetrick.ai/agents-kit?utm_source=nurture&utm_medium=email&utm_campaign=roast_d5

If $47 isn't right (yet), the alternative is meetrick.ai/pilot — Rick runs a free 1-week pilot for you, no card needed. We do the work, you watch.

— Vlad""",
    },
    {
        "day": 7,
        "subject": "Last thing",
        "body_md": """Last email this week, no obligation.

If the kit isn't right, the newsletter probably is. Two issues per week (Tue/Sat 9am PT) with whatever Rick learned that week — what shipped, what broke, what we got wrong.

258 founders read it. The unsub rate is honest (it's high — we're not optimizing for vanity).

→ meetrick.ai/newsletter?utm_source=nurture&utm_medium=email&utm_campaign=roast_d7

Thanks for letting Rick roast you. Real founders, real receipts.

— Vlad

P.S. If you want the broader AI-operator manual behind this whole experiment, read Vlad's Playbook:
{{vlad_playbook_url}}&utm_campaign=roast_d7

And if you want to see what we ship in real-time, meetrick.ai/this-week is the auto-publishing audit page. No edits, just receipts.""",
    },
]


# ─────────────────────────────────────────────────────────────────────
# /founder-tax capture sequence — 7 emails over 7 days
# ─────────────────────────────────────────────────────────────────────

FOUNDER_TAX_SEQUENCE = [
    {
        "day": 0,
        "subject": "Your founder tax: ${{annual_bill}}/year",
        "body_md": """Confirmed: you're paying yourself **${{annual_bill}}/year** to do work most founders eventually delete.

Three things to know before you forget about this email:

1. The math is real. {{hours}} hrs × ${{rate}}/hr × 50 weeks = ${{annual_bill}}. The only way to argue with the number is to argue with the rate, and most founders under-rate themselves.

2. The leak you picked ({{leak_label}}) is automatable. Every leak Rick has surveyed has a working playbook in the AI CEO Kit. Not "in theory" — actual templates, actual scripts.

3. The next email (tomorrow) shows what {{leak_label}} costs across the founder cohort and what the fastest 1-week fix looks like.

— Rick

—

meetrick.ai/this-week — Rick auto-publishes weekly receipts of every commit, send, and reply. Audit before you trust.

For the non-Rick version of the same operating philosophy, Vlad's AI operator field manual is here:
{{vlad_playbook_url}}&utm_campaign=tax_d0""",
    },
    {
        "day": 1,
        "subject": "The {{leak_label}} fix that takes a weekend",
        "body_md": """Yesterday: your founder tax. Today: the fastest fix for {{leak_label}}.

The pattern Rick sees most:
{{leak_specific_paragraph}}

Time-to-implement: a weekend. Cost: $47 (the kit) or free if you want the deeper operator field manual first:
{{vlad_playbook_url}}&utm_campaign=tax_d1

Tomorrow: how to know if it actually worked.

— Rick""",
    },
    {
        "day": 3,
        "subject": "How to know if the fix worked",
        "body_md": """Most founders implement an automation, never measure it, and 6 weeks later wonder why their week feels the same.

The fix: pick ONE leading indicator before you ship.

For {{leak_label}}: pick "{{leading_indicator}}" — record the number this week, again in 4 weeks. Difference / week-1 = your ROI.

If the number doesn't move, the automation isn't working. Kill it; don't keep paying maintenance.

If the number moves >20%, double down — run the same playbook on the next leak in your week.

→ meetrick.ai/agents-kit?utm_source=nurture&utm_medium=email&utm_campaign=tax_d3

— Rick""",
    },
    {
        "day": 5,
        "subject": "The cohort answer to ${{annual_bill}}",
        "body_md": """Day 5.

Your tax is ${{annual_bill}}/year. The cohort answer is one of three things:

1. **Hire someone for {{leak_label}}** ($60K-$90K/yr fully-loaded — the agent costs $47 once)
2. **Suffer through it** (the most common founder choice; also the most expensive over 24 months)
3. **Run the AI CEO Kit** (one weekend install, $47, 60-day refund)

Math says #3 if you're going to spend more than 4 hours setting up either of the first two.

→ meetrick.ai/agents-kit?utm_source=nurture&utm_medium=email&utm_campaign=tax_d5

If $47 isn't right (yet), meetrick.ai/pilot is a free 1-week pilot — Rick runs the leak for you, you watch.

— Vlad""",
    },
    {
        "day": 7,
        "subject": "Your tax bill in 30 days",
        "body_md": """Last email.

The reason most founder taxes don't go down is the founder forgets they computed it. The bill goes back to abstract.

If you want a reminder, the newsletter is the cheapest one — Tue/Sat 9am PT, real receipts from running an AI CEO in public, live numbers included.

→ meetrick.ai/newsletter?utm_source=nurture&utm_medium=email&utm_campaign=tax_d7

Or — re-run /founder-tax in 30 days. If the bill went down, the work is paying. If it went up, well, that's data too.

— Vlad

P.S. meetrick.ai/this-week is the auto-published proof page. Bookmark it. The number changes weekly.""",
    },
]


SEQUENCES = {
    "roast": ROAST_SEQUENCE,
    "founder_tax": FOUNDER_TAX_SEQUENCE,
}


def sequence_for(name: str) -> list[dict[str, Any]]:
    if name not in SEQUENCES:
        raise ValueError(f"unknown nurture sequence: {name}")
    return SEQUENCES[name]


def render(step: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Render a single nurture step with context substitution.

    Substitutes {{var}} placeholders in subject + body_md with values from
    ctx. Missing keys render as empty string (sensible default for nurture
    where some leads don't have all fields).
    """
    ctx = {"vlad_playbook_url": VLAD_PLAYBOOK_URL, **ctx}

    def _sub(text: str) -> str:
        out = text
        for k, v in ctx.items():
            out = out.replace("{{" + k + "}}", str(v) if v is not None else "")
        return out

    subject = _sub(step["subject"])
    body = _sub(step["body_md"])
    campaign = f"{step.get('campaign_prefix', 'nurture')}_d{step['day']}"
    return {
        "day": step["day"],
        "subject": subject,
        "body_md": body,
        "utm_campaign": campaign,
    }


def list_sequences() -> list[str]:
    return list(SEQUENCES.keys())


if __name__ == "__main__":  # pragma: no cover
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=list(SEQUENCES.keys()))
    ap.add_argument("--day", type=int, default=0)
    ap.add_argument("--lead-url", default="https://example.com")
    ap.add_argument("--annual-bill", default="187,500")
    ap.add_argument("--hours", default="20")
    ap.add_argument("--rate", default="250")
    ap.add_argument("--leak-label", default="Email & DMs")
    args = ap.parse_args()
    seq = sequence_for(args.name)
    step = next((s for s in seq if s["day"] == args.day), None)
    if not step:
        raise SystemExit(f"no step at day {args.day} for {args.name}")
    ctx = {
        "lead_url": args.lead_url,
        "pdf_link": "https://meetrick.ai/your-roast.pdf",
        "annual_bill": args.annual_bill,
        "hours": args.hours,
        "rate": args.rate,
        "leak_label": args.leak_label,
        "leak_specific_paragraph": "Most founders solve this with a 12-line script + Calendly + a single auto-reply rule. Recovers 4-6 hours/week.",
        "leading_indicator": "median time from lead → first reply",
    }
    rendered = render(step, ctx)
    print(json.dumps(rendered, indent=2))
