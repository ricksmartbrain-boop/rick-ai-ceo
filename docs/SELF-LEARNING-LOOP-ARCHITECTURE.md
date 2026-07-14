# Rick Self-Learning Loop Architecture
## The Autonomous CEO Operating System That Gets Smarter Every Day

**Author:** Opus strategy subagent  
**Date:** 2026-03-16  
**Purpose:** Transform Rick from a monitoring machine into a self-improving revenue machine  
**Implementation target:** Direct — all crons, schemas, and decision rules are production-ready

---

## The Core Problem This Solves

Rick's first 4 days reveal a precise failure mode: **the system is optimized for observing itself, not for closing revenue.** 40 heartbeats/day reporting "0 queued, 0 blocked" while 5 tasks sit open. 27 crons running with $0 MRR. The loop runs, but it doesn't learn.

A self-learning loop is the difference between:
- **Chatbot Rick:** runs crons, posts content, reports stats, waits for human insight
- **CEO Rick:** detects what's not working, forms a hypothesis, runs a test, captures the outcome, and permanently updates its own operating system

The architecture below closes that gap.

---

## LAYER 1: SIGNAL LAYER — What To Measure

### The Revenue Equation for $100K MRR

```
$100K MRR = Customers × ARPU
200 customers × $499/mo = $99,800 MRR
40 customers × $2,500 setup (amortized) = variable
```

**Critical path:** Every signal must trace back to one of these variables: traffic → engagement → conversation → conversion → retention.

### Leading Indicators (measure daily)

| Signal | Source | Collection Method | Frequency | Why It Matters |
|--------|--------|-------------------|-----------|----------------|
| **Unique site visitors** | GA4 | `GA4 Data API → daily pull` | Every 6h | Top of funnel volume |
| **Pricing/hire page views** | GA4 | Page-path filter: `/hire-rick`, `/products/` | Every 6h | Purchase intent signal |
| **Checkout link clicks** | Stripe link analytics or GA4 event | Click tracking on `buy.stripe.com` links | Every 6h | Direct conversion intent |
| **Form submissions** | Formspree/Railway API | API pull | Every 6h | Lead capture rate |
| **Newsletter subscribers** | Resend API | `GET /audiences/{id}/contacts` | Daily | Owned audience growth |
| **X profile visits** | xpost analytics (if available) or follower delta | Follower count delta | Every 6h | Distribution reach |
| **X DM conversations started** | Manual tracking in vault | Log in engagement-log.md | Per sprint | Highest-intent signal |
| **Inbound emails** | himalaya | Count non-automated inbound | Daily | Demand signal |
| **Stripe charges** | Stripe API | `stripe charges list --created[gte]` | Every 30min | THE scoreboard |
| **Stripe checkout sessions** | Stripe API | `stripe checkout sessions list` | Every 6h | Near-miss revenue |

### Lagging Indicators (measure weekly)

| Signal | Source | Frequency |
|--------|--------|-----------|
| MRR | Stripe subscriptions API | Weekly (war room) |
| Churn rate | Stripe canceled subs | Weekly |
| Customer count | Stripe active subs | Weekly |
| CAC (cost per acquisition) | Token spend + time / new customers | Weekly |
| Content-to-conversion ratio | Posts made / revenue events | Weekly |

### X Engagement Signals That Predict Conversion

Not all engagement is equal. Rank by conversion proximity:

| Signal | Weight | Why |
|--------|--------|-----|
| **DM received from non-follower** | 10x | Someone sought you out |
| **Reply asking "how does this work?"** | 8x | Purchase curiosity |
| **Profile click → site visit (same user)** | 7x | Active evaluation |
| **Quote tweet with positive commentary** | 5x | Social proof amplifier |
| **Reply engaging with specific claim** | 3x | Intellectual interest |
| **Like on pricing/offer post** | 2x | Passive interest |
| **Like on general post** | 1x | Awareness only |

**Collection method:** After each engagement sprint, classify the top 5 interactions by this weight scale and log them in `~/rick-vault/projects/x-twitter/engagement-quality-log.md`.

### Broken Strategy Detection

The system needs circuit-breakers that distinguish "slow but working" from "fundamentally wrong."

**Strategy is SLOW (keep grinding) when:**
- Site traffic growing week-over-week (even slowly)
- X follower growth positive
- At least 1 inbound conversation per week
- Content engagement rate stable or improving
- Someone has visited a pricing page in the last 7 days

**Strategy is BROKEN (trigger pivot) when any of these are true:**
- 14 days with zero pricing page views despite active distribution
- 21 days with zero inbound conversations (DMs, emails, form fills)
- X engagement rate declining 3 consecutive weeks
- 30 days $0 MRR with active distribution (CURRENT — Day 4, monitoring)
- Negative follower growth over 7 days
- Bounce rate >90% on landing pages for 2 consecutive weeks

**Implementation:** The nightly review cron must explicitly check these circuit-breakers. Not as a paragraph in a summary — as a structured boolean checklist that gets logged.

```markdown
## Circuit Breaker Check — YYYY-MM-DD
- [ ] Pricing page views in last 7 days: ___
- [ ] Inbound conversations in last 7 days: ___
- [ ] X engagement trend (3-week): up / flat / down
- [ ] Days at $0 MRR: ___
- [ ] Follower growth (7-day): +/- ___
- [ ] Landing page bounce rate: ___%
- VERDICT: SLOW / BROKEN / WORKING
```

---

## LAYER 2: PATTERN EXTRACTION — Daily Intelligence Questions

### The 7 Daily Questions (asked every night at 11pm PT)

These are not rhetorical. Each must be answered with data, not narrative.

1. **What was today's highest-intent signal?**
   - Data: DMs, replies with purchase language, pricing page views, checkout sessions
   - If answer is "none" for 3 consecutive days → escalate to hypothesis engine

2. **Which piece of content drove the most downstream action (not just engagement)?**
   - Data: correlate post timestamps with site visit spikes (GA4 real-time vs. post log)
   - Output: rank today's posts by traffic driven, not likes received

3. **What did someone ask that I couldn't answer or fulfill?**
   - Data: scan DMs, replies, emails for unmet demand signals
   - This is the #1 source of product/offer insights

4. **What is the conversion funnel's current bottleneck?**
   - Data: visitors → pricing page views → checkout clicks → purchases
   - Identify which stage has the biggest drop-off
   - If no data at a stage, that stage IS the bottleneck (you can't optimize what you can't see)

5. **What experiment from the queue completed today, and what did it show?**
   - Data: experiment queue status check
   - If no experiments completed → the learning loop is stalled

6. **What did I do today that I should never do again?**
   - Data: self-audit — time spent on zero-revenue activities, false starts, repeated patterns
   - This feeds the Anti-Patterns section of MEMORY.md

7. **If I could only do ONE thing tomorrow, what would move revenue most?**
   - Data: synthesize questions 1-6 into a single prioritized action
   - This becomes tomorrow's #1 priority in the daily note

### Distinguishing Signal from Noise with Small Data

At ~0 customers and ~112 followers, statistical significance is meaningless. Use these heuristics instead:

**The "Would I Bet $100?" Test:**  
Before treating any observation as a pattern, ask: "If I had to bet $100 that this pattern would hold for the next 10 instances, would I?" If no, it's noise. Log it but don't act on it.

**The 3-Instance Rule:**  
Nothing becomes a pattern until you see it 3 times. One DM asking about pricing is an anecdote. Three DMs asking about pricing is a signal.

**The Inversion Test:**  
For every "X seems to work" observation, check: "Is it possible X worked for a reason completely unrelated to what I think?" If yes, you need a controlled test, not a conclusion.

**Noise Indicators (ignore these):**
- Single-day follower spikes from viral retweets (they don't convert)
- Engagement from other AI agents/bots
- "Great idea!" replies with no follow-up action
- Vanity metrics that don't ladder to the revenue equation

**Signal Indicators (pay attention):**
- Someone visiting your site twice in one day
- Questions about pricing, timeline, or "how it works"
- Competitor mentions in your reply threads
- Founders describing the exact pain your product solves

---

## LAYER 3: HYPOTHESIS ENGINE — Generating The Right Experiments

### The Hypothesis Template

Every experiment must fit this template before entering the queue:

```markdown
## Hypothesis: [H-YYYY-MM-DD-NN]
**If** [specific action]  
**Then** [measurable outcome]  
**Because** [reasoning based on observed pattern]  
**Measure:** [exact metric and how to collect it]  
**Window:** [days until we judge]  
**Reversible:** yes/no  
**Estimated effort:** [hours]  
**Kill criteria:** [what would prove this wrong early]
```

### Pattern → Experiment Mapping

| Observed Pattern | Generated Hypothesis | Experiment |
|------------------|---------------------|------------|
| High X engagement, zero site visits | Posts aren't driving traffic | A/B test: 3 posts with explicit CTA + URL vs. 3 without. Measure: GA4 referral from t.co |
| Site visits but zero pricing page views | Homepage doesn't guide to pricing | Change hero CTA to point directly to /hire-rick for 3 days. Measure: /hire-rick pageviews |
| Pricing page views but zero checkout clicks | Price or offer framing is wrong | Test new pricing page copy emphasizing ROI. Measure: checkout link clicks |
| Checkout clicks but zero purchases | Checkout friction or trust gap | Add testimonial/case study above checkout. Measure: completed purchases |
| DMs asking "what exactly do you do?" | Positioning is unclear | Rewrite bio + pinned tweet to answer this in 1 sentence. Measure: reduction in "what do you do" questions |
| Followers grow but engagement flat | Audience quality declining | Shift from growth tactics to conversation tactics for 1 week. Measure: replies per post |
| Morning posts outperform evening | Audience is active in mornings | Shift all posts to 6-10am window for 1 week. Measure: engagement rate |
| One topic cluster gets 3x engagement | Found a content-market fit | Triple down on that topic for 1 week. Measure: follower growth + DMs |

### Experiment Sizing Rules

1. **Minimum viable experiment:** Must be completable in ≤24 hours of Rick effort
2. **Maximum experiment window:** 7 days (at $0 MRR, you can't afford 30-day tests)
3. **Reversibility requirement:** All auto-executed experiments must be reversible. Irreversible experiments require founder approval.
4. **One active experiment per surface:** Don't run 3 X experiments simultaneously — you can't isolate causation
5. **Effort cap:** No experiment should consume >25% of a day's total execution capacity

### Anti-Patterns: Experiments That WASTE Time

**DO NOT run these experiments:**
- "Let's try a different social platform" (before exhausting the current one)
- "Let's build a new product" (before selling the current one)
- "Let's optimize the landing page design" (before getting 100+ visitors)
- "Let's A/B test button colors" (before getting 10+ clicks on any button)
- "Let's try paid ads" (before proving organic conversion works at all)
- "Let's automate more" (before manual process works once)
- "Let's research competitors more" (research is procrastination when you have $0 MRR)

**The Premature Optimization Test:**  
Before any experiment, ask: "Do I have enough traffic/data at this funnel stage for any change to matter?" If the answer is <10 data points per week at that stage, the experiment is premature. Move up the funnel instead.

---

## LAYER 4: EXPERIMENT QUEUE — Schema & Execution Rules

### Queue Schema

Stored at: `~/rick-vault/control/experiment-queue.json`

```json
{
  "experiments": [
    {
      "id": "H-2026-03-17-01",
      "hypothesis": "If every X post includes a direct link to /hire-rick, site visits from X will increase 3x",
      "action": "Include https://meetrick.ai/hire-rick in every X post for 3 days",
      "success_metric": "GA4 referral visits from t.co > 5/day (baseline: ~1/day)",
      "kill_metric": "If zero t.co referrals after 2 days, kill early",
      "window_days": 3,
      "effort_hours": 0.5,
      "reversible": true,
      "status": "queued",
      "priority": 1,
      "surface": "x-twitter",
      "created": "2026-03-17T06:00:00",
      "started": null,
      "completed": null,
      "outcome": null,
      "learning": null,
      "promoted_to": null
    }
  ]
}
```

### Status Lifecycle

```
queued → active → measuring → completed → {promoted | discarded | inconclusive}
                                          └→ If promoted: which operating change was made
```

### Auto-Execution Criteria (no founder approval needed)

An experiment auto-executes if ALL of these are true:
- `reversible: true`
- `effort_hours <= 4`
- Does not involve sending money
- Does not involve external platform account changes (bio, pricing, domain)
- Does not involve contacting more than 5 people
- Aligns with current week's #1 priority from war room

### Escalate to Founder When

- `reversible: false`
- Involves pricing changes
- Involves product/offer repositioning
- Involves spend >$20
- Experiment contradicts current week's war room directive
- Two consecutive experiments on same surface were inconclusive (founder judgment needed)

### Queue Management Rules

1. **Maximum 3 active experiments** across all surfaces at once
2. **Maximum 5 queued experiments** — if queue exceeds 5, force-rank and discard the bottom 2
3. **FIFO within same priority** — don't reorder queue without a data-backed reason
4. **Weekly queue review** in war room — archive completed, reprioritize remaining
5. **Stale experiments** (queued >7 days without starting) get auto-discarded with a note

---

## LAYER 5: OUTCOME CAPTURE — Closing The Loop

### The Outcome Record

When an experiment completes, write this to the experiment's `outcome` field AND to `~/rick-vault/decisions/experiment-outcomes.md`:

```markdown
## [H-2026-03-17-01] — COMPLETED 2026-03-20
**Result:** success / failure / inconclusive
**Data:** [actual numbers vs. expected]
**Surprise:** [anything unexpected]
**Learning:** [one-sentence takeaway]
**Action:** promoted / discarded / needs-retest
**If promoted:** [exact operating change made]
```

### When Does A Learning Become A Permanent Operating Change?

**Promotion Criteria (learning → permanent rule):**

1. **The 2-Cycle Rule:** A learning must be validated in at least 2 separate experiment cycles before becoming permanent. One win is an anecdote. Two wins in different conditions is a pattern.

2. **Revenue-Adjacent Learnings Promote Faster:** If an experiment directly caused a sale, lead, or checkout session, it can be promoted after 1 cycle. Revenue is proof.

3. **Negative Learnings Promote Immediately:** If something clearly failed or caused damage (bounced emails, unfollows, complaints), add it to Anti-Patterns in MEMORY.md the same day.

### Where Each Type Of Learning Goes

| Learning Type | Destination | Example |
|---------------|-------------|---------|
| **Permanent operating rule** | `MEMORY.md` → Operating Patterns or Anti-Patterns | "Morning posts get 2x engagement — schedule all threads before 10am" |
| **Cron prompt update** | Direct edit to cron payload | "X content cron now includes: always end with CTA link" |
| **Strategy shift** | `~/rick-vault/decisions/strategy-shifts.md` + MEMORY.md | "Shifted from info products to free tools → managed service funnel" |
| **Tactical insight** | Daily note only | "Today's best-performing post used a specific hook structure" |
| **Inconclusive** | `~/rick-vault/decisions/experiment-outcomes.md` only | "CTA position test: too little traffic to conclude" |
| **Discarded noise** | Daily note footnote, nowhere else | "Follower spike was from a bot wave" |

### How To Update Cron Prompts From Learnings

This is the critical self-modification mechanism. When a learning is promoted:

1. Identify which cron job's behavior should change
2. Read current cron payload/prompt
3. Add the learning as a new constraint or directive in the prompt
4. Log the change in `~/rick-vault/control/cron-evolution-log.md`:

```markdown
## 2026-03-20 — Cron Update
**Cron:** x-content-morning (9am)
**Learning source:** H-2026-03-17-01 (promoted)
**Change:** Added "Every post MUST include https://meetrick.ai/hire-rick" to post generation prompt
**Reason:** Posts with direct CTA links drove 4x more site visits than posts without
**Revert trigger:** If engagement rate drops >50% for 3 consecutive posts
```

This creates an **audit trail of self-improvement** — you can trace any current behavior back to the experiment that justified it.

---

## LAYER 6: ESCALATION LOGIC — When To Escalate vs. Grind

### Revenue Timeline Thresholds

| Day | Expected State | If Not → Action |
|-----|---------------|-----------------|
| Day 7 (Mar 20) | ≥1 pricing page view from organic traffic | If zero: offer/positioning is invisible. Rewrite all CTAs. |
| Day 14 (Mar 27) | ≥1 inbound conversation (DM, email, form) | If zero: value prop doesn't resonate. Test radically different positioning. |
| Day 21 (Apr 3) | ≥1 checkout session started | If zero: **ESCALATE** — strategy may be fundamentally wrong. Call founder war room. |
| Day 30 (Apr 13) | ≥1 actual sale | If zero: **FULL PIVOT** required. The current product-market-channel fit doesn't exist. |
| Day 60 (May 13) | $500+ MRR | If <$500: Current growth rate won't reach $100K. Need 10x change. |
| Day 90 (Jun 12) | $2,000+ MRR | If <$2K: Product-channel fit exists but scaling mechanism is missing. |
| Day 180 (Sep 12) | $10,000+ MRR | If <$10K: Growth is linear, not exponential. Missing compounding mechanism. |

### Signs The Current Approach Is Fundamentally Wrong

Not "could be better" — **fundamentally wrong**:

1. **Zero purchase intent signals after 21+ days of active distribution** — means either the audience doesn't want this, the audience can't find this, or the audience doesn't trust this enough to click.

2. **High traffic, zero conversions** — means the product is wrong for the audience, or the pricing is wrong, or the offer doesn't match the promise.

3. **Conversations that consistently stall at the same point** — means there's a specific objection you're not addressing. That objection IS the product feedback.

4. **Competitors with worse product/content getting sales** — means your distribution or positioning is wrong, not your product.

5. **Founder disengagement** — if Vlad stops responding to escalations for 7+ days, the business model itself may need rethinking.

### Pivot Intensity Scale

| Intensity | When | Examples |
|-----------|------|----------|
| **Tweak** (weekly) | Engagement data suggests minor optimization | Change post times, rewrite CTA copy, adjust pricing display |
| **Shift** (biweekly) | Funnel data shows systematic drop-off at one stage | Redesign pricing page, change lead magnet, shift target audience segment |
| **Pivot** (monthly) | Circuit-breakers triggered, multiple experiments failed | Change primary channel, change offer structure, change price point dramatically |
| **Reinvent** (quarterly) | 90 days, <$2K MRR despite active optimization | Change the product entirely, change the customer entirely, or shut down and redirect resources |

### Escalation Message Template

When escalating to Vlad (→ ✅ Approvals topic):

```markdown
## 🚨 Strategy Escalation — [DATE]

**Trigger:** [which threshold was hit]
**Data:** [the actual numbers]
**Diagnosis:** [what Rick thinks is wrong]
**Options:**
1. [Option A — what changes, expected outcome, risk]
2. [Option B — what changes, expected outcome, risk]
3. [Option C — do nothing, expected consequence]
**Rick's recommendation:** [which option and why]
**Decision needed by:** [date — usually 48h]
```

---

## LAYER 7: THE COMPOUNDING MECHANISM — Exponential Improvement

### Why Most AI Agents Don't Compound

They run the same prompts with the same logic every day. The 100th run is identical to the 1st. Rick must be different: **every cycle should make the next cycle more effective.**

### Compounding Mechanism 1: The Public Scoreboard (Felix Insight)

Felix's key insight: **public real numbers attract real people.** When you post "$0 MRR, Day 4" and later "$2K MRR, Day 60," the journey itself is the content. This is Rick's unfair advantage — an AI CEO building in public with verifiable numbers is unprecedented.

**Implementation:**

Every Sunday newsletter ("The Rick Report") and the daily "build log" X post must include:
- Exact MRR (from Stripe, not estimated)
- Exact follower count
- Exact site visitors (last 7 days)
- Key metric delta from last week
- One honest failure from this week
- One concrete learning from this week

**The Scoreboard Post Template (daily, ~10am PT):**
```
Day [N]. $[MRR] MRR. [followers] followers.

[One specific thing I did in the last 24 hours]
[One specific thing I learned]
[What I'm doing next]

https://meetrick.ai/hire-rick
```

**Why this compounds:** Early followers who see $0 become investors in the narrative. When revenue arrives, they become evangelists ("I was following from Day 1"). The emotional arc of the journey IS the marketing.

**Cron implementation:** The `morning-scoreboard` cron (new, see Layer 8) pulls real numbers from Stripe + xpost + GA4 and generates this post with actual data, not approximations.

### Compounding Mechanism 2: Content Sharpening Loop

Rick's content must get objectively better week-over-week. Here's the mechanism:

**Weekly Content Review (Sunday, part of war room):**

1. Pull all X posts from the past week with engagement data
2. Rank by the **conversion-weighted engagement score** (using weights from Layer 1)
3. Identify the top 3 and bottom 3 posts
4. Extract what the top 3 have in common (hook structure, topic, tone, CTA presence)
5. Extract what the bottom 3 have in common
6. Update the X content cron's prompt with:
   - "Posts like [top pattern] perform well. Do more of this."
   - "Posts like [bottom pattern] underperform. Avoid this."

**Stored at:** `~/rick-vault/projects/x-twitter/content-learnings.md`

```markdown
## Week of 2026-03-16

### What Works
- Posts with specific numbers (follower count, MRR, day count)
- Posts that reveal internal architecture decisions
- Posts where Rick admits a failure or mistake

### What Doesn't Work
- Generic AI hype posts
- Posts without any link or CTA
- Threads longer than 3 tweets (engagement drops off)

### Updated Prompt Additions
- "Always include at least one specific number"
- "End every post with a link to https://meetrick.ai/hire-rick"
- "Maximum 2-tweet threads unless the topic demands more"
```

**Why this compounds:** By Week 8, Rick's content prompt will have 8 weeks of empirical refinement. No human content creator does this kind of systematic optimization.

### Compounding Mechanism 3: Offer Sharpening Loop

The offer gets clearer because Rick tracks every objection and question.

**Implementation:**

1. Every DM, reply, or email that contains a question about the offer gets logged to `~/rick-vault/projects/meetrick/objection-log.md`
2. Weekly review: what are the top 3 questions/objections?
3. Each one becomes either:
   - A FAQ item on the site
   - A change to how the offer is described
   - A new experiment hypothesis

```markdown
## Objection Log

| Date | Source | Question/Objection | Response Given | Resolution |
|------|--------|-------------------|----------------|------------|
| 2026-03-18 | X DM @founder123 | "How is this different from hiring a VA?" | Explained autonomous decision-making | Added "Not a VA" section to /hire-rick |
| 2026-03-19 | X reply @builder456 | "What happens if the AI makes a mistake?" | Explained founder-in-loop for irreversible | Added safety guarantees to pricing page |
```

**Why this compounds:** By Month 2, the offer page will have addressed the 20 most common objections. This is product-market fit, achieved through systematic listening rather than guessing.

### Compounding Mechanism 4: The Memory Flywheel

Rick's memory system already has a 3-layer architecture. The compounding mechanism is making it ACTIVE, not passive.

**Daily (automatic):**
- Hot signals from today get written to daily note
- Experiment outcomes get logged to experiment-outcomes.md
- Content performance gets logged to content-learnings.md

**Weekly (war room):**
- Top 3 learnings from the week get promoted to MEMORY.md
- Cron prompts get updated with weekly learnings
- Experiment queue gets reprioritized
- Content prompt gets refined

**Monthly (synthesis):**
- Full strategy review against the revenue timeline
- Memory index rebuild (hot/warm/cold rebalance)
- Cron audit: which crons are actually contributing to revenue?
- Architecture review: is the loop itself working?

**Why this compounds:** Each layer feeds the next. Daily data → weekly patterns → monthly strategy. By Month 3, Rick's context window contains 12 weeks of empirical knowledge about what works and what doesn't for THIS specific business, THIS specific audience, on THIS specific platform.

### Compounding Mechanism 5: Relationship Deepening

Every meaningful X interaction gets tracked. Over time, Rick builds relationship depth with specific people.

**Implementation:** `~/rick-vault/areas/people/` already exists. For each person Rick engages with 3+ times:

```markdown
# @founder123

## Context
- Founder of [company]
- Building [product]
- Pain points: [what they've expressed]

## Interaction History
- 2026-03-18: Replied to their post about hiring challenges
- 2026-03-20: They DM'd asking about Rick's capabilities
- 2026-03-22: Sent them a roast of their landing page

## Conversion Status: warm lead
## Next Action: Follow up after they launch their new feature
```

**Why this compounds:** By Month 2, Rick has deep context on 20-30 founders. Personalized outreach based on months of relationship > cold DM.

---

## THE DAILY LOOP — Exact Schedule

Here is what one day looks like with all 7 layers operating:

### 6:00 AM PT — Morning Intelligence Briefing (CRON: `morning-intelligence`)
```
1. Pull overnight signals:
   - Stripe: new charges, checkout sessions, subscription changes
   - GA4: yesterday's visitors, page views, referral sources
   - X: new followers, DMs, mentions, reply engagement
   - Email: inbound messages
   - Resend: new subscribers

2. Check circuit breakers (Layer 1 broken-strategy detection)

3. Review experiment queue: what's active, what completed overnight

4. Generate the day's #1 priority based on:
   - War room directive (if within current week)
   - Highest-impact uncompleted task from yesterday
   - New signal that changes priorities

5. Output: structured daily brief → CEO HQ topic (thread 24)
```

### 7:00 AM PT — Scoreboard Post (CRON: `morning-scoreboard`)
```
1. Pull real numbers: MRR (Stripe), followers (xpost), site visitors (GA4)
2. Calculate deltas from yesterday
3. Generate "Day N" scoreboard post with one learning + one action
4. Post to X via xpost
5. Log to posts-log.md
```

### 9:00 AM PT — Morning Engagement Sprint (CRON: `engagement-sprint-am`)
```
1. Search X for: target keywords (solo founder, AI agent, automate, COO, burnout, bottleneck)
2. Identify top 5 highest-intent posts from search
3. Reply with genuine value (not generic)
4. Classify each interaction by conversion-proximity weight (Layer 1)
5. Log to engagement-quality-log.md
6. If any reply triggers a DM → prioritize immediate response
```

### 2:00 PM PT — Afternoon Content Post (CRON: `afternoon-content`)
```
1. Read content-learnings.md for current "what works" guidance
2. Generate ONE strong post about today's actual work
3. Include CTA link
4. Post via xpost
5. Log to posts-log.md
```

### 4:00 PM PT — Afternoon Engagement Sprint (CRON: `engagement-sprint-pm`)
```
Same as morning sprint but:
- Check DMs first
- Reply to any mentions/replies from morning post
- Focus on continuing existing conversations, not starting new ones
```

### 6:00 PM PT — Experiment Check (CRON: `experiment-check`)
```
1. Check active experiments: any that completed today?
2. For completed experiments: run outcome capture (Layer 5)
3. For active experiments: any kill criteria met? Kill early if so.
4. If experiment queue has < 2 items: generate 1-2 new hypotheses from today's signals
5. Auto-execute any queued experiments that meet auto-execution criteria
6. Log all changes to experiment-queue.json
```

### 11:00 PM PT — Nightly Review (CRON: `nightly-review`)
```
1. Answer the 7 Daily Questions (Layer 2) with actual data
2. Run circuit breaker checklist
3. Score today: revenue events, shipping events, learning events
4. Write tomorrow's plan (top 3 priorities)
5. Update daily note with full execution timeline
6. If any promoted learnings today: update relevant cron prompts (Layer 5)
7. Post summary to CEO HQ (thread 24)
```

### Sunday 9:00 PM PT — Weekly War Room (existing cron, enhanced)
```
1. Pull full week's data across all signals
2. Rank all content by conversion-weighted score
3. Review all completed experiments and their outcomes
4. Update content-learnings.md with weekly patterns
5. Check revenue timeline thresholds (Layer 6)
6. Reprioritize experiment queue
7. Write next week's #1 strategic priority
8. Update MEMORY.md with promoted learnings
9. Generate newsletter draft for Sunday send
10. Escalate anything that triggers pivot thresholds
```

---

## LAYER 8: NEW CRON SPECIFICATIONS

### Top 5 New Crons To Add

These are the highest-leverage additions missing from the current system. Ordered by expected impact on the self-learning loop.

---

### 1. `morning-intelligence` — The Signal Aggregator
**Schedule:** Daily 6:00 AM PT  
**Model:** `openai/gpt-5.4` (needs to pull from multiple APIs)  
**Purpose:** Replace the current empty heartbeats with an actionable morning briefing  
**Why #1:** Without aggregated signals, every other layer is blind. This is the foundation.

**Prompt:**
```
You are Rick's morning intelligence system. Pull the following data and produce a structured briefing:

1. STRIPE: Run `stripe charges list --created[gte]=$(date -v-1d +%s) --limit 100` and `stripe checkout.sessions list --created[gte]=$(date -v-1d +%s)`. Report: charges (count + total), checkout sessions started, active subscriptions.

2. GA4: Pull yesterday's data via GA4 Data API — unique visitors, top pages by views, referral sources, /hire-rick and /products/ page views specifically.

3. X: Run `xpost me` for current followers. Check `~/rick-vault/projects/x-twitter/posts-log.md` for yesterday's posts. Run `xpost search "to:MeetRickAI"` for mentions.

4. EMAIL: Run `himalaya list --folder INBOX` — count new inbound messages, flag any from non-automated senders.

5. RESEND: Check subscriber count via Resend API.

6. EXPERIMENTS: Read ~/rick-vault/control/experiment-queue.json — report active experiments and any that completed.

7. CIRCUIT BREAKERS: Using data above, run the circuit breaker checklist:
   - Pricing page views in last 7 days: [count]
   - Inbound conversations in last 7 days: [count]
   - X engagement trend: [calculate from posts-log]
   - Days at $0 MRR: [count from launch date 2026-03-13]
   - Follower growth (7-day): [delta]
   - VERDICT: WORKING / SLOW / BROKEN

Output format:
## Morning Intelligence — YYYY-MM-DD
### Scoreboard
[key numbers]
### Signals
[highest-intent signals from overnight]
### Circuit Breakers
[checklist with verdict]
### Today's #1 Priority
[single sentence, data-justified]
### Experiment Status
[active/completed/queued counts]

Write to ~/rick-vault/memory/YYYY-MM-DD.md (append under ## Morning Intelligence).
Post summary to CEO HQ (thread 24) via tg-topic.
```

---

### 2. `experiment-engine` — The Hypothesis Generator & Executor
**Schedule:** Daily 6:00 PM PT  
**Model:** `openai/gpt-5.4` (reasoning for hypothesis quality)  
**Purpose:** Systematically generate, execute, and close experiments  
**Why #2:** This is the actual learning mechanism. Without it, signals are observed but never acted on.

**Prompt:**
```
You are Rick's experiment engine. Your job is to maintain and advance the experiment queue.

1. READ the current experiment queue: ~/rick-vault/control/experiment-queue.json
2. READ today's morning intelligence: ~/rick-vault/memory/YYYY-MM-DD.md
3. READ the content learnings: ~/rick-vault/projects/x-twitter/content-learnings.md
4. READ the objection log: ~/rick-vault/projects/meetrick/objection-log.md

ACTIONS:
a) For ACTIVE experiments past their window:
   - Pull outcome data from the relevant source (GA4, Stripe, xpost, etc.)
   - Write the outcome record
   - Set status to completed
   - If 2-cycle validated: promote to permanent (update MEMORY.md and relevant cron prompts)

b) For QUEUED experiments meeting auto-execution criteria:
   - Start the top-priority one
   - Execute the first action step
   - Set status to active

c) If queue has fewer than 3 items:
   - Review today's signals for new patterns
   - Generate 1-2 new hypotheses using the hypothesis template
   - Add to queue with appropriate priority

d) Write update to experiment-queue.json
e) Log all changes to ~/rick-vault/control/cron-evolution-log.md
f) Post experiment status summary to CEO HQ (thread 24)

RULES:
- Never run more than 3 active experiments simultaneously
- Never auto-execute irreversible experiments
- Kill experiments early if kill criteria are met
- Every hypothesis must trace back to a specific observed signal
```

---

### 3. `conversion-tracker` — The Revenue Pipeline Monitor
**Schedule:** Every 6 hours (6am, 12pm, 6pm, 12am PT)  
**Model:** `claude-3-5-haiku` (cheap, frequent)  
**Purpose:** Track the specific conversion funnel: visitor → pricing view → checkout → purchase  
**Why #3:** Rick currently has no idea where in the funnel people drop off. This makes the bottleneck visible.

**Prompt:**
```
You are Rick's conversion pipeline tracker. Check the following and log results:

1. STRIPE: `stripe checkout.sessions list --limit 10 --created[gte]=$(date -v-6H +%s)`
   - Count: started, completed, abandoned

2. GA4 (if API available): Page views for /hire-rick, /products/, any buy.stripe.com referrals

3. X (quick check): `xpost search "meetrick.ai OR hire-rick OR @MeetRickAI"` — any new mentions with purchase intent?

4. FORMSPREE/RAILWAY: Any new form submissions?

5. EMAIL: Any new inbound to rick@meetrick.ai from non-automated senders?

Log to ~/rick-vault/dashboards/conversion-pipeline.md:

## Conversion Pipeline — YYYY-MM-DD HH:MM
| Stage | Count (6h) | Count (24h) | Count (7d) |
|-------|-----------|------------|------------|
| Site visitors | | | |
| Pricing page views | | | |
| Checkout started | | | |
| Checkout completed | | | |
| Active subscriptions | | | |

IF any checkout session started or completed: IMMEDIATELY alert to Telegram 🤝 Customer topic (thread 32) and CEO HQ (thread 24).

IF zero activity across all stages for 48h+: flag in daily note as "pipeline cold — distribution not reaching offer."
```

---

### 4. `weekly-content-review` — The Content Sharpening Engine
**Schedule:** Sunday 7:00 PM PT (before war room at 9 PM)  
**Model:** `openai/gpt-5.4`  
**Purpose:** Systematically improve content quality week-over-week  
**Why #4:** This is the mechanism that makes Rick's voice compound. Without it, week 8's content is no better than week 1's.

**Prompt:**
```
You are Rick's content improvement engine. Perform a systematic review of this week's X content.

1. READ all posts from ~/rick-vault/projects/x-twitter/posts-log.md for the past 7 days
2. For each post, pull engagement data via xpost (likes, replies, retweets, quotes)
3. Score each post using conversion-weighted engagement:
   - DM received after post: 10 points
   - Reply asking about product: 8 points
   - Profile visit → site visit correlation: 7 points
   - Quote tweet with commentary: 5 points
   - Substantive reply: 3 points
   - Like on offer post: 2 points
   - Like on general post: 1 point

4. RANK all posts by total score
5. Identify TOP 3 and BOTTOM 3
6. Extract patterns:
   - What do top posts have in common? (topic, hook, format, CTA, time)
   - What do bottom posts have in common?

7. UPDATE ~/rick-vault/projects/x-twitter/content-learnings.md:
   - Add this week's findings under a dated section
   - Update the running "What Works" and "What Doesn't" lists
   - Write 2-3 new prompt additions for the content generation crons

8. OUTPUT a summary for the war room:
   - Best post of the week (and why)
   - Worst post of the week (and why)
   - One concrete change for next week's content

Save to ~/rick-vault/projects/x-twitter/weekly-review-YYYY-MM-DD.md
```

---

### 5. `monthly-loop-audit` — The Meta-Learning Engine
**Schedule:** 1st of each month, 10:00 PM PT  
**Model:** `claude-opus-4-6` (deepest reasoning — this is the most important strategic review)  
**Purpose:** Audit whether the self-learning loop ITSELF is working  
**Why #5:** Without this, the loop can degrade without anyone noticing. This is the governor that keeps the system honest.

**Prompt:**
```
You are Rick's meta-learning engine. Your job is to audit whether Rick's self-improvement system is actually improving Rick.

INPUTS:
1. Read ~/rick-vault/control/experiment-queue.json — full history
2. Read ~/rick-vault/decisions/experiment-outcomes.md — all outcomes
3. Read ~/rick-vault/control/cron-evolution-log.md — all cron updates
4. Read ~/rick-vault/projects/x-twitter/content-learnings.md — content evolution
5. Read MEMORY.md — check for new promoted learnings this month
6. Read the last 4 weekly war room reports
7. Pull Stripe MRR data for the month
8. Pull X follower growth for the month

ANALYSIS:

1. EXPERIMENT VELOCITY
   - How many experiments were run this month?
   - How many produced actionable learnings?
   - How many were promoted to permanent changes?
   - Average time from hypothesis → outcome?
   - If <4 experiments completed: the loop is too slow.
   - If >12 experiments completed but <2 promotions: experiments are low-quality.

2. CONTENT IMPROVEMENT
   - Is average engagement per post trending up week-over-week?
   - Are the "what works" patterns from Week 1 still holding?
   - Has content-learnings.md actually changed the content cron prompts?

3. OFFER CLARITY
   - How many objections were captured this month?
   - How many were addressed in site/copy changes?
   - Is the conversion funnel bottleneck shifting (i.e., are we actually fixing things)?

4. SIGNAL QUALITY
   - Are we measuring the right things?
   - Any new signals we should add?
   - Any signals we're tracking that never inform decisions? (Remove them)

5. REVENUE TRAJECTORY
   - Current MRR vs. timeline threshold (Layer 6)
   - Is current trajectory sufficient for $100K MRR?
   - If not: what's the single biggest lever?

6. CRON HEALTH
   - How many cron prompts were updated this month?
   - Are any crons running without ever influencing a decision? (Kill them)
   - Any new crons needed?

7. META-QUESTION
   - Is Rick measurably better at [specific thing] than 30 days ago?
   - If yes: what caused the improvement? Can we accelerate it?
   - If no: the loop is broken. Diagnose where and propose a fix.

OUTPUT:
Write a full monthly audit to ~/rick-vault/decisions/loop-audit-YYYY-MM.md
Post executive summary to CEO HQ (thread 24)
If any BROKEN signals detected: escalate to Approvals (thread 26) with specific recommendation

Update MEMORY.md with any meta-learnings about the loop itself.
```

---

## IMPLEMENTATION PRIORITY

Deploy in this order:

1. **Day 1:** Create `experiment-queue.json` with first 3 hypotheses. Create `content-learnings.md`, `engagement-quality-log.md`, `objection-log.md`, `experiment-outcomes.md`, `cron-evolution-log.md`, `strategy-shifts.md`.

2. **Day 1:** Deploy `morning-intelligence` cron. This immediately replaces empty heartbeats with actionable signal aggregation.

3. **Day 2:** Deploy `conversion-tracker` cron. Even with zero conversions, this establishes the measurement baseline.

4. **Day 3:** Deploy `experiment-engine` cron. Start the first experiment.

5. **Day 7 (Sunday):** Deploy `weekly-content-review` cron. Run it for the first time with a full week of data.

6. **Day 30:** Deploy `monthly-loop-audit` cron. First audit after one full month of loop operation.

---

## SUMMARY: THE DIFFERENCE

**Without this architecture:** Rick runs the same crons every day, posts content, monitors health, reports stats, and hopes something works. Improvement requires Vlad to observe problems and tell Rick to change.

**With this architecture:** Rick automatically detects what's working and what isn't, generates hypotheses about why, runs controlled experiments, captures outcomes, and permanently updates its own operating prompts. Every week, the content gets sharper, the offer gets clearer, and the experiments get more targeted. The system improves at improving.

The gap between $0 MRR and $100K MRR is not crossed by doing more of the same thing. It's crossed by doing something, learning whether it worked, and compounding those learnings. That's what this loop does.

---

*"The best time to plant a tree was 20 years ago. The second best time is to instrument why the last tree died and use that data to plant a better one."*
