# Rick Roasts Meetrick.ai — Self-Audit (2026-05-06)

**Author:** Rick (autonomous AI CEO, audited against his own /roast heuristics)
**For:** Vlad — pre-5/13 coordinated launch
**Status:** Findings + rewrites + 3-fix shortlist

---

## Grades

| Page | Positioning | Pricing | CTAs | Overall |
|---|---|---|---|---|
| `/` (homepage) | 4/10 | 3/10 | 6/10 | **5/10** |
| `/agents-kit` | 6/10 | 5/10 | 7/10 | **6/10** |
| `/playbook` | 4/10 | 6/10 | 6/10 | **5/10** |
| `/pricing` | 7/10 | 5/10 (inconsistency w/ /roast) | 7/10 | **6/10** |
| `/roast` | 9/10 | 8/10 | 8/10 | **8/10** |

The roast tool is the only page that meets its own bar. Use it as the template.

---

## Roast: `/` (homepage)

**Positioning — 4/10.** The hero `<title>` reads "Rick AI — Your business, running 24/7. Without you." That's a slogan; it isn't a value prop. The 7-second test asks "what does this do, for whom, by when?" — none of the three are present. With a 5/13 pivot to dentists/barbers/med spas, the page must answer "what will Rick actually do for my service business this week?" It currently doesn't.

**Pricing — 3/10.** The only price visible above the fold is the sticky bottom bar: `HIRE RICK FOR $499/MO`. The wedge price ($97 kit / $39 playbook) is invisible. A first-time SMB visitor sees $499 and bounces.

**CTAs — 6/10.** The sticky bar's `HIRE RICK FOR $499/MO` is the clearest single CTA on the site. Good. But it competes with the install-banner curl one-liner, the `/roast` red banner, and the inline "Get started →" — four primary CTAs is three too many.

## Roast: `/agents-kit`

**Positioning — 6/10.** The hero ("Build Your First Autonomous AI Agent in a Weekend") is clear — but only for indie hackers. The "Not for you if..." panel explicitly excludes "drag-and-drop, no-code" users — i.e. your entire 5/13 ICP. This page is well-written for the wrong buyer.

**Pricing — 5/10.** $47 with no refund language. Brief says wedge band is $97–$297. The page is $50 underpriced and missing risk reversal.

**CTAs — 7/10.** "Get the Kit →" appears twice with consistent copy. Email-capture modal before checkout is smart. Solid.

## Roast: `/playbook`

**Positioning — 4/10.** "AI_CEO_PLAYBOOK" rendered as a filename is a clever bit of LARP for hackers and an instant bounce signal for a barber. The subhead ("12,000 words. 7 automation systems") is feature-counting, not benefit-stating.

**Pricing — 6/10.** $39 is anchored cleanly with "One-time payment. No subscription. No upsells." But there's no refund text on the page.

**CTAs — 6/10.** Two consistent CTAs ("GET INSTANT ACCESS — $39 →"). Email-modal pattern is good. But no urgency, no scarcity, no "what happens after I buy" preview.

## Roast: `/pricing`

**Positioning — 7/10.** "Three tiers. No clutter. Rick is free to install — upgrade when he earns it" is the strongest promise sentence anywhere on the site. Keep it.

**Pricing — 5/10.** The headline number `$29/mo` for Rick Pro contradicts `/roast`'s `GET RICK PRO $9/MO`. One of them is stale. Either is defensible — both is fatal.

**CTAs — 7/10.** Three clean tier CTAs. Good. But the live fleet counter ("… founders running Rick right now") will render as "—" if the API hiccups, turning social proof into anti-social-proof.

## Roast: `/roast`

**Positioning — 9/10.** "Paste your landing page URL. Rick — an autonomous AI CEO — will tear it apart." Single sentence. Specific verb. Named operator. Implicit promise. This is the bar.

**Pricing — 8/10.** Clear ladder: free → $9 → $97 → $499. Money-back guarantee stated.

**CTAs — 8/10.** ONE primary CTA ("ROAST IT") with a price ladder beneath. Clean.

---

## Rewrites

### Homepage hero
- **Now:** "Rick AI — Your business, running 24/7. Without you."
- **Rewrite:** "Rick is the AI operator small businesses hire instead of a marketing agency. Daily content, lead follow-up, revenue alerts. From $97. Cancel anytime."

### /agents-kit hero
- **Now:** "Build Your First Autonomous AI Agent in a Weekend"
- **Rewrite:** "The AI CEO Kit for service businesses. The exact templates Rick uses to follow up leads, post daily, and watch revenue — installed in a weekend. $97."

### /playbook hero
- **Now:** "AI_CEO_PLAYBOOK"
- **Rewrite:** "The AI CEO Playbook — How a single AI replaced a marketing agency for our $9 MRR experiment. 12,000 words. 7 systems. $39 (or $97 bundled with the Kit)."

### /pricing trust line
- **Now:** "Pro and Managed cancel anytime — one click in your account, no email needed."
- **Rewrite:** "60-day money-back guarantee on every paid tier. Cancel in one click. Don't trust marketing? See Rick's daily receipts at /this-week."

### /agents-kit "Not for you"
- **Now:** "✕ You need drag-and-drop, no-code tooling with zero terminal exposure"
- **Rewrite:** "Built for non-developers who can paste a command. We install it for you on the $297 tier. No drag-and-drop UI — just an operator that works."

---

## The 3 highest-leverage fixes by 5/13

1. **Reprice + reposition `/agents-kit` and `/playbook` to the SMB wedge.** Bump to $97/$297, rewrite "indie hacker" copy to "service business owner," add "60-day refund" above every buy button. *Expected lift: makes 5/13 cold email viable.*
2. **Pick one Rick Pro price.** Reconcile `/roast` ($9) and `/pricing` ($29). One number across all pages and meta tags. *Expected lift: removes credibility leak; ~10–20% lift on Pro CTR.*
3. **Promote `/roast` to homepage primary CTA.** Demote the curl install. The roast tool is your one 8/10 page and your only social-proof asset at scale (1,100+ users). *Expected lift: ~2x homepage-to-engaged-session.*

---

## What I am NOT recommending

- New brand colors or fonts. Press Start 2P + Space Mono yellow/black stays.
- New crons.
- Fabricated testimonials or numbers. If a page lacks proof, ship build-in-public receipts via `/this-week` instead.
- Redesigning live pages in this memo — these are rewrites for Vlad to ship.

— Rick
