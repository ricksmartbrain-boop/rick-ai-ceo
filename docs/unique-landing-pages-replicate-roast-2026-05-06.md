# Replicate /roast Traffic Pattern — 3 New Unique Landing Pages (2026-05-06)

**Author:** Plan agent (Opus) synthesized from /roast pattern analysis
**Goal:** Compound the /roast traffic loop (1,100+ founders, 5+ min avg) with 3 new tool-as-landing-pages that drive same share-and-funnel mechanics.
**Constraint:** Each page must satisfy the 6 heuristics: free + no-signup + <60s, share-worthy personalized output, pull-not-push input, honest output, funnels into a real product, Rick's voice.

---

## The 3 Winners

### 1. `/founder-tax` (HIGHEST LEVERAGE — ship first)

Calculate "what your founder busywork actually costs in $/year." Pure indie-hacker share-bait. Every founder posts the screenshot of their own tax bill.

### 2. `/replace-this-role`

Paste a job description. Rick scores 0–100 on AI-replaceable + 30/60/90-day replacement plan. Lands SMB ICP cold (front-desk replacement) and indie-hacker ICP warm (VA replacement).

### 3. `/ai-readiness/{industry}`

24 permalink pages, one per industry (dentist, barber, med spa, chiro, law firm, agency, etc.). 5-axis scorecard + 30-day playbook preview. The SMB compounding-traffic monster (SEO + share double-loop).

---

## `/founder-tax` — Full Spec (the strongest)

**Why strongest:** purest screenshot artifact (a $-figure with your name), zero input friction (3 numbers), brutally honest math (deterministic), hands directly into /agents-kit.

### Hero (paste-ready)

- **H1:** `WHAT'S YOUR FOUNDER TAX?`
- **Sub:** Every hour you spend on ops is an hour you don't spend shipping. Rick computes what your busywork actually costs — in dollars, per year. Free. No signup. 10 seconds.
- **CTA button:** `CALCULATE THE BILL`
- **Microcopy:** 1,100+ founders got roasted. Now find out what your week is costing you.

### Input (3 fields, one row)

1. Hours/week on ops (number, 0–80, default 20)
2. Your effective rate $/hr (number, default 150, helper: `Founder? Use $250. Engineer? Use $200.`)
3. Biggest leak (dropdown: Email/DMs · Scheduling · Customer follow-up · Reporting/dashboards · Content · Hiring/recruiting · Other)

### Result page sections

1. **THE BILL** — giant headline number: `$XXX,XXX / YEAR` (= hours × rate × 50). Below: `That's [X] months of runway. [Y] hires you didn't make.`
2. **THE LEAK** — one paragraph specific to dropdown choice. Static-templated, not LLM-hallucinated. 7 templates, 1 per dropdown value.
3. **WHAT RICK REPLACES** — 4-row table: leak → Rick capability → est. hrs/wk recovered → $/year recovered. Deterministic math from inputs.
4. **THE ONE FIX** — single LLM-generated paragraph (gpt-5.5 or sonnet, 100 tokens) seeded with leak choice. Sharp, founder-to-founder, names a specific automation pattern.
5. **RICK'S VERDICT** — one-liner stamp: `You're paying yourself $X/yr to do $15/hr work. Stop.`

### Share trigger

The giant `$187,500/YEAR` headline on yellow with the dot-grid is screenshot gold. Add an `og:image` endpoint at `/api/founder-tax-og?h=20&r=250&leak=email` that renders a 1200×630 PNG of just the bill — auto-previews when shared on X/LinkedIn/Slack. **The shared image IS the post.**

### Funnel hand-off (bottom of result)

- Primary: `STOP THE BLEED — GET THE AI CEO KIT ($97) →` to /agents-kit
- Secondary: `WANT RICK TO RUN IT? PILOT FREE FOR A WEEK →` to /pilot
- Tertiary: `Calculate someone else's tax →` (loop)
- Share: `Post my tax bill on X` (pre-filled tweet with their number + URL)

### Build complexity

**EASY.** 1 HTML file (`/founder-tax/index.html`), 1 serverless handler (`/api/founder-tax.js`, ~90 lines, mostly deterministic + 1 small LLM call), 1 OG image endpoint (`/api/founder-tax-og.js`, ~60 lines using `@vercel/og` or canvas). No DB. Mirror `/api/roast.js` exactly.

### Traffic profile

Indie Hackers milestone-thread shares, X founder accounts, r/SaaS, r/Entrepreneur. Strong cross-post potential — screenshot needs zero context.

### Distribution plan

1. X thread from @stbelkins: "I just calculated my own founder tax. $312k/yr. Here's the math."
2. IH milestone post.
3. Newsletter issue.
4. Reply-bomb every "I'm drowning in ops" tweet for 72h with the link.

---

## `/replace-this-role` — Quick Spec

**Build:** MEDIUM (~2 days). Input: textarea (paste JD) OR title+5 duties. Output: 0–100 score, 4 tasks AI does today, 3 tasks AI cannot, 30/60/90-day replacement plan, cost delta. Share trigger: HR/founders posting "Rick gave my JD a 73/100" on LinkedIn (LinkedIn-native, unlike /roast which is X-native). Funnel: /agents-kit + /pilot.

---

## `/ai-readiness/{industry}` — Quick Spec

**Build:** MEDIUM (~3 days). Dropdown of 24 industries → permalink page per industry (SEO compound). Output: 5-axis radar (Intake/Scheduling/Follow-up/Reviews/Reporting) scored against industry baseline, 3 specific automations for that industry, preview of /playbook. Share trigger: SMB owners posting "my industry scored 2.4/5 on AI readiness" — but real win is Google ("ai readiness for dentists" → page #1 in 60 days because permalink). Funnel: /playbook ($39) and /agents-kit ($97).

---

## May-13 Launch Primary?

**No — keep `/this-week` as primary** per coordinated-launch-playbook PART B. Don't break the Show HN / IH narrative arc.

**But:** ship `/founder-tax` on **Mon May 11** as a *warm-up asset*. It seeds the 72h window (HN crowd shares their tax bills Tue–Wed), and the Wed 5/13 X thread can open with: "Yesterday 400 founders calculated their tax. Today the agent that runs my company published its own receipts." Two artifacts pointing at each other = launch compounds harder.

**If `/this-week` fails the May 12 staleness check**, `/founder-tax` becomes the **fallback launch primary** — no staleness risk because it's pure user-input math.

---

## Build Effort Summary

| Page | Days | Difficulty | Ship by |
|---|---|---|---|
| `/founder-tax` | 1 | Easy | Mon 5/11 (pre-launch warm-up) |
| `/replace-this-role` | 2 | Medium | Wed 5/20 (post-launch) |
| `/ai-readiness/{industry}` | 3 | Medium | Mon 6/2 (compounding SEO play) |
| **Total to ship all 3** | **5–6 days** | — | First by 5/11 |

---

## Critical Files for Implementation

- `/Users/rickthebot/meetrick-site/api/roast.js` — pattern to clone for each new endpoint
- `/Users/rickthebot/meetrick-site/roast/index.html` — pattern to clone for each new HTML page
- `/Users/rickthebot/meetrick-site/agents-kit/index.html` — funnel target for /founder-tax + /replace-this-role
- `/Users/rickthebot/meetrick-site/playbook/index.html` — funnel target for /ai-readiness
- `/Users/rickthebot/.openclaw/workspace/docs/coordinated-launch-playbook-2026-05-06.md` — the May-13 plan that /founder-tax warms up
