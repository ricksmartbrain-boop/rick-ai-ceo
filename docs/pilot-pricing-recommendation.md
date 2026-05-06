# Pilot pricing — what tier the Day-7 CTA should pitch

**Date:** 2026-05-04
**Audience:** Vlad (operator), Rick (Day-7 CTA generator), pricing-page never-touched
**Decision:** Day-7 CTA pitches **$499 Pro only**. Drop $99 from the pilot funnel entirely. Keep $1500 Custom as a *response* to "this isn't enough", never as the opening.

## The data — top 5 cold-DM targets

From `~/rick-vault/projects/vlad-dms/icp-batch-2-2026-05-05/INDEX.md`. Public revenue cited in their own posts / X profiles / Indie Hackers byline:

| Rank | Founder | Product | Public revenue | Tier they can afford painlessly |
|------|---------|---------|----------------|---------------------------------|
| 1 | Cameron Trew | Kleo + Mentions | $62K MRR + $20K MRR (cap-table partner) | $1500 Custom |
| 2 | Richard Wang | LeadMore | $30K MRR / $1M ARR run-rate | $1500 Custom |
| 3 | Iuliia Shnai | Papermark | $500K → $1M ARR in 6 months | $1500 Custom |
| 4 | Jon Yongfook | Bannerbear | $52K MRR solo | $1500 Custom |
| 5 | Rashid Khasanov | Angelmatch + 3 others | 4-product portfolio, $20K+ aggregate | $499 Pro |

Median across the 30-DM batch: **$15K–$30K MRR**. P10: ~$500/mo. P90: ~$60K MRR.

## Why $99 actively hurts in this funnel

1. **Anchoring problem.** A founder doing $30K MRR who sees $99 reads it as "this is for hobbyists." Rick is positioned as an autonomous CEO. CEO ≠ $99/mo. The starter tier signals the wrong altitude.
2. **Selection problem.** A $99 buyer asks for $99-of-effort. The cold-DM batch was chosen specifically *not* to attract that buyer. We'd be re-creating the segment we're trying to leave.
3. **Margin problem.** A pilot week consumes ~$15-30/day of LLM spend (per `reference_rick_v6_token_costs.md`). Five $99 customers = ~$500 MRR but ~$1500-3000/mo in LLM costs alone. $99 is below variable cost at meaningful pilot intensity.
4. **The $9 MRR data point.** The single existing customer is on a legacy tier. Forty-three days of $9 MRR is the strongest evidence we have that the $99 tier doesn't pull a buyer who values the product.

## Why $1500 Custom is also wrong as the opener

1. **Decision fatigue.** A Day-7 founder just gave Rick a free week. Asking them to commit $18K/year on Day 8 is a different sale — needs a call, needs a contract, needs procurement.
2. **Pilot signal mismatch.** The pilot proved Rick can run *one lane* (cold outbound to a defined ICP). Custom implies *multiple lanes*. We haven't proven multiple lanes in week 1.
3. **Reactive only.** If the founder says "$499 isn't enough — I want X, Y, Z too", THAT is the moment to say "Custom $1500 covers that, here's a 30-min call." Never lead with it.

## The recommendation

**Day-7 CTA copy (drop into `pilot-deliverable.py` Day-7 generator when built):**

> Want Rick to keep running this lane? **$499/mo Pro** — starts Monday, same scope as your pilot week, you stay in control of approvals. Reply 'in' or 'out'. One word.

**What to test in the next 30 days:**

1. **Default Day-7 CTA: $499 Pro only.** Single price, single word reply. Track conversion rate from Day-7 summary → "in" reply.
2. **Reactive $1500 escalation.** When a founder says "I want Rick on lane B too" or "I want this for two products", quote $1500 Custom in a 1-line reply. Don't put it on the pilot page.
3. **Kill $99 from the cold-DM funnel.** $99 stays on the public pricing page (don't touch — Vlad's standing rule), but the pilot CTA never references it. If a founder explicitly asks "what's the cheapest option", reply: "$99 starter exists for self-serve, but the pilot you just ran is sized for the $499 lane. Up to you."
4. **Measure cost-of-revenue per pilot.** If pilot LLM cost is consistently >$300/founder, the $499 price is right; raise to $599 only if conversion holds.
5. **Re-evaluate at 5 paid Pro conversions.** That's the point at which we have signal on whether founders renew at month 2 or churn. If renewal < 60%, the issue is delivery not pricing — don't touch the price.

## What NOT to do

- Don't change the public pricing page. Vlad's standing rule. Tested or not, the homepage / pricing / nav stays as-is.
- Don't add a "yearly" option to the pilot CTA. One choice on Day 7. Yearly is a Day 30 conversation.
- Don't bundle the pilot with a discount code. The pilot was already free; "free + 50% off month 1" reads like a sale and undermines the autonomous-CEO frame.
- Don't add a $299 tier between $99 and $499 to "ladder up". Three prices is exactly the wrong number. Four is worse.

## One-sentence summary

**Pitch only $499 Pro on Day 7; keep $1500 Custom in your back pocket for the founder who says "$499 isn't enough"; drop $99 from the pilot funnel entirely because it signals the wrong altitude to a $30K-MRR ICP.**
