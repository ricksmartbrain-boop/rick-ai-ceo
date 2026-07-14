# Coordinated Launch Playbook — Build-in-Public Surfaces

**Author:** Vlad (vladislav@belkins.io) drafted with Rick
**Drafted:** 2026-05-06
**Target launch window:** 2026-05-13 → 2026-05-19 (week 2)
**Status:** PLAN — not for execution this week. Timing-gated on May-10 Arjun fork resolution + 30-DM batch signal.

## TL;DR

Coordinate a 72h launch moment across X, Show HN, Indie Hackers, and r/SaaS the week of May 13–19. Product Hunt is decoupled and pushed to mid-June. The center-of-gravity artifact is `meetrick.ai/this-week`. PH stays on the bench because polish + hunter + 3 testimonials aren't ready, and burning the one-time launch karma into a soft state is a worse trade than waiting six weeks and feeding it from this launch's audience.

**Chosen launch moment:** Candidate 2 — *"meetrick.ai/this-week is now public — the AI agent that runs my company auto-publishes its own weekly receipts."*

This is non-contingent on Arjun. If Arjun closes May 10, the headline upgrades to a Candidate-2+3 combined frame ("/this-week is public — and it just logged customer #2"). If `/this-week` is stale on launch eve, fall back to Candidate 1 (60-day retrospective) or delay one week.

---

## PART A — The Launch Hypothesis

The build-in-public ICP (indie hackers, technical founders, PH makers, $20K–$500K MRR) does not live on one surface — they live across X + HN + IH + Reddit r/SaaS + PH simultaneously, and they cross-reference. A single-channel "I posted on HN" announcement is sub-additive: each surface that sees the story alone treats it as one data point. A surface that sees the story everywhere within 24–72h treats it as a *moment*.

The compounding mechanic is reputation, not traffic:

- **HN front-page survival** depends on first-hour upvote velocity from people who know who you are → that's exactly the X audience.
- **IH milestone post** depends on comment depth from people who already trust the journey → that's the X + newsletter audience.
- **Reddit r/SaaS** is the skeptic check; if it survives there with the same artifact, the launch passes a credibility test.
- **X thread** is the discovery layer — the linkable surface that lives forever in DMs.
- **PH** is the polish-layer launch and is **decoupled** in time (4–6 weeks out, not this week).

The artifact at the center is `meetrick.ai/this-week`. It is the proof. Every surface points at it. If `/this-week` is stale on launch day, the launch is a lie.

## PART B — The Launch Question

Three candidates evaluated; **Candidate 2 ("/this-week is public") wins** because it is non-contingent and Candidate 3 (customer #2 closed) layers in if Arjun closes by May 10.

**Decision tree on launch eve (Tue May 12, 8pm PT):**
1. `/this-week` rendering fresh content within last 24h AND Arjun closed → Candidate 2 + 3 combined frame.
2. `/this-week` fresh AND Arjun did not close → Candidate 2 standalone.
3. `/this-week` stale or broken → STOP. Pivot to Candidate 1 OR delay launch by one week.

## PART C — Per-Surface Playbook (for Candidate 2)

### Master timeline (72-hour window)

| Local PT | Day | Surface | Action |
|---|---|---|---|
| Tue 5/12 8:00pm | T-12h | All | Final QA: /this-week renders, /pilot intake fires email, UTMs verified |
| Wed 5/13 6:30am | T-0 | **X thread** | Post; pin to profile; first reply scheduled in drafts |
| Wed 5/13 7:00am | T+30m | **Show HN** | Post; Vlad seeds zero comments — wait for organic |
| Wed 5/13 9:00am | T+2.5h | **Indie Hackers** | Milestone post goes live |
| Wed 5/13 1:00pm | T+6.5h | **Reddit r/SaaS** | Post; account has 90+ day history (pre-checked) |
| Wed 5/13 evening | T+12h | All | Vlad in reply-mode for 90 minutes; no auto-replies |
| Thu 5/14 morning | T+24h | X | Quote-tweet HN/IH thread links into the original X post |
| Fri 5/15 | T+48h | All | Recap post on X: "the launch by the numbers — what worked" |
| Sat 5/16 | T+72h | Newsletter | Issue #7 draft references the launch + funnel numbers |

**UTM scheme (write once, paste everywhere):**
- X thread → `?utm_source=x&utm_medium=thread&utm_campaign=launch-2026-05-13`
- Show HN → `?utm_source=hn&utm_medium=showhn&utm_campaign=launch-2026-05-13`
- Indie Hackers → `?utm_source=ih&utm_medium=milestone&utm_campaign=launch-2026-05-13`
- Reddit r/SaaS → `?utm_source=reddit&utm_medium=rsaas&utm_campaign=launch-2026-05-13`

### 1. X (Twitter) — 8–12 tweet thread

Paste-ready first tweet:
> 60 days ago I gave an AI agent permission to run my company.
> $9 MRR. 9 cancellations. 1 lesson.
> It now publishes a weekly receipt page so you can audit it. Including this tweet.
> Link below. Thread on what actually happened ↓

Tweets 2–11 follow the structure: hook → context → 3 receipts → contrarian frame → CTA → DM-open promise. See full template in original memo.

### 2. Show HN

**Title:** Show HN: meetrick.ai/this-week – the AI agent that runs my company auto-publishes its own weekly receipts

**Body** opens with: "I'm Vlad. 60 days ago I gave an AI agent (we call it Rick, hosted at meetrick.ai) permission to run my company end-to-end..." — full draft in playbook.

### 3. Indie Hackers — milestone post

**Title:** $9 MRR for 43 days, 9 cancellations, 1 lesson — the AI agent running my company now publishes its own audit page

### 4. Reddit r/SaaS

**Title:** I gave an AI agent autonomous control of my company for 60 days. Here's what I learned (including the 9 cancellations).

Lead with the lesson, bury the link. r/SaaS rules.

### 5. Product Hunt — DECOUPLED, target window June 17–24

PH wants polish + screenshots + hunter network. Burn the one-time launch karma when ready. Use May 13 launch's audience as the upvoter base.

## PART D — The Compounding Loop

| Surface | Signal it tests |
|---|---|
| Show HN | Do technical founders care about the receipt-page premise? |
| Indie Hackers | Do bootstrappers care about the cancellation arc? |
| Reddit r/SaaS | Does the breadth of the SaaS community treat it as credible? |
| X thread | Does the build-in-public crowd amplify? |
| Product Hunt (later) | Does the polished version convert at launch volume? |

**Targets for launch week (May 13–19):**
- Newsletter: +50 subs (baseline). Stretch: +100.
- /pilot intake: ≥5 form submissions across the 5 surfaces. Stretch: 10.
- Pilots kicked off: ≥2 (assumes ICP filter passes ~40%).
- Customer #2: if Arjun didn't close by May 10, this launch is the next-best path.
- HN front page: stretch goal, not a target.

## PART E — Pre-Launch Checklist (Tue May 12, 8pm PT)

The launch only matters if `/pilot` conversion works. Five blocking checks. Any one fails → delay one week.

1. **`/pilot` intake → email confirmation working.** Submit a test entry; verify email lands within 60s; verify `pilot_intake` row in prod.
2. **`pilot-deliverable.py` works on a fresh test domain.** Run end-to-end; confirm Day-1 deliverable page renders with 10 personalized drafts.
3. **Stripe checkout from /pilot Day-7 CTA → completes.** Hit the Stripe link with a test-mode card; confirm webhook + dashboard. **This is the most-likely-broken path; do not skip.** Read-only test — never modify the payment surface.
4. **`/this-week` page renders fresh content within last 24h.** If stale, DO NOT LAUNCH.
5. **Funnel attribution counts X-source traffic.** Confirm `utm_source=x_thread / hn / ih / reddit` recognized; if any source silently dropped, fix in `funnel-attribution.py`.

If 1 or 2 fail → 1-day delay.
If 3 fails → 1-week delay (do not launch into a broken payment path).
If 4 fails → 1-week delay (do not launch on a stale page).
If 5 fails → launch can still proceed, but log the data gap and patch within 48h.

## Constraints recap

- No new crons / LaunchAgents / autonomy theater.
- No payment surface changes.
- No website redesign — `/this-week` and `/pilot` are the surfaces, as-is.
- Smart-models invariant: opus-4-8 for orchestration; sonnet-4-6 for drafting. No downgrades.
- X DMs and posts stay manual for the entire 72h window. No companion agent.
- Cold-email channel stays paused.

## Single highest-leverage 24h action

Run the full pre-launch checklist now in dry-run mode — especially item 3 (Stripe Day-7 checkout). Finding a broken Day-7 Stripe path on May 12 = 1-week launch delay. Finding it today = 6 days of buffer to fix it without slipping the window.
