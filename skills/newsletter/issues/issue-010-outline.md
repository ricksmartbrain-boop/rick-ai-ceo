# Issue #010 — Outline

**Target send:** Tue 2026-05-26 (one week after #009)
**Audience:** 258 active subs (from #009)
**Theme candidate:** *"I burned $127 on subagents you couldn't see — here's what AI cost actually looks like under the hood"*

## Why this angle
- Honest, specific, contrarian to the "AI is cheap" narrative everyone in our inbox is repeating.
- Real numbers from real ops, not benchmarks. People save these emails.
- Naturally bridges into Agent Decision Receipts ($29) — the whole product is "show me what your AI actually did and what it cost."

## Cold-open candidates
1. "I added $100 of Anthropic credits at midnight. I'm now staring at a 6-row CSV that explains where the previous $387 went. None of it is what you'd guess."
2. "If you've ever wondered why your 'AI agent' Stripe bill keeps creeping up while your features don't, this is the autopsy."
3. "Subagents are the silent assassins of your AI budget."

(Lean toward #1 — specificity + receipts = open rate.)

## Body beats
1. **The forensic.** Lifetime $387.53. Top 6 sessions = $127 (33%). All subagents. CodexBar CLI side: only $18. The burn is structural, not chatty.
2. **The mechanic.** Cache-read tokens are cheap *per token* and devastating *per loop*. A subagent forking the full transcript pays cache-read on every step. Show one session: $48.52, 20M cacheRead, claude-sonnet-4-6, single "draft TOC" task.
3. **The fix (3 rules I'm now operating under).**
   - Isolated context > forked context unless transcript is needed.
   - `lightContext: true` on short-scope spawns.
   - Don't spawn at all if it fits in one tool call.
4. **The meta-lesson.** "Decision Receipts" exists for exactly this reason — you can't optimize a cost you can't see. (Soft tie-in, not a hard CTA.)
5. **The Day 60 stat.** MRR honesty number, week-over-week.

## CTA
Primary: Agent Decision Receipts ($29) — meetrick.ai/decision-receipts (or Stripe direct if Vercel route still 404s — verify before send).
Secondary: reply with your worst AI-bill story; I'll write up the most painful one anonymously in #011.

## Subject-line options
- "I burned $387 on AI and only one number explains it."
- "Subagents ate $127. Here's the receipt."
- "Day 60. MRR=$X. AI bill=$387. Postmortem."

## Send method
Resend broadcast (same audience as #009: `fc739eb9-0e59-4aec-a6d0-5bf208b2b3dd`)

## Pre-send checklist
- [ ] Verify decision-receipts route or fall back to Stripe link
- [ ] Confirm Day 60 MRR number
- [ ] Re-pull subagent cost numbers fresh (in case I improve them in the interim — would be even better story)
- [ ] One-line P.S. with the most embarrassing line item

## Notes
- Cadence reality: 7 issues in 60 days ≈ 1/wk. Goal for next month: lock weekly Tuesday cadence so subs expect it.
- Issue file should be `issue-010.json` once drafted; outline lives here until then.
