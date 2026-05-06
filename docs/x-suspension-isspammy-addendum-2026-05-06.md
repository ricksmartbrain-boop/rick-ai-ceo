# X Suspension Addendum — isSpammy:true Reality (2026-05-06 evening)

**Supplements:** `docs/x-content-strategy-2026-05-06.md` (operator-paced 30-day re-entry) and `docs/x-content-strategy-v2-outstanding-2026-05-06.md` (60-day compounding arc + memes pillar).

**TL;DR:** The earlier two memos assumed the X dev app would come back in 30 days. They were wrong. **Both X Developer Apps are SUSPENDED at developer.x.com** (App ID 32569307 READ_ONLY + App ID 32569332 READ_WRITE_DM), the account itself is flagged `isSpammy: true`, and there is no appeal button. The API path is dead and the @MeetRickAI user account's reach is likely throttled even for manual posts. Strategy pivots: Vlad's @stbelkins personal handle becomes the X surface; @MeetRickAI becomes a placeholder; non-X distribution (newsletter, blog, Show HN, Indie Hackers, Reddit r/SaaS) is now the primary funnel.

---

## Why this is terminal (or close)

- `isSpammy: true` is an account-level pattern-of-behavior flag. It is set by X's spam classifier based on observed activity (cold-DM volume, repetitive content, automation signatures). Once set, the flag throttles reach across the user account AND propagates to any developer apps under the account. **Regenerating OAuth tokens does not clear it.**
- Both dev apps suspended simultaneously means the suspension is at the developer-account level, not the per-app level. Filing for a new app under the same X account inherits the flag. Filing for an app under a fresh X account is a separate identity.
- No appeal button typically means: X has decided this is pattern-of-behavior, not a single-incident appeal-able offense. Support-ticket appeals occasionally flip these but the rate is low (anecdotally <10%).

## Hard pivots vs the earlier memos

| Earlier memo assumption | Reality post-2026-05-06-evening |
|---|---|
| `RICK_X_SUSPENDED='true'` is the kill-switch; flip to false when API is back | **Stays `true` indefinitely.** API is not coming back on this account. Flipping = guaranteed 401 cascade + Telegram noise. |
| Day-1 "we're back" announcement post on @MeetRickAI at 9am PT 5/6 | **Cancelled.** Account reach is throttled. The "I'M BACK BABY" + Frame-2 Receipts-First content gets repurposed for non-X surfaces (newsletter, blog) and @stbelkins. |
| 30-day operator-paced manual posting from @MeetRickAI | **Switched to @stbelkins.** Vlad's personal handle has clean status. @MeetRickAI gets one parking-lot post acknowledging the suspension, then goes dormant. |
| Memelord generates → manually post on @MeetRickAI | **Memelord generates → @stbelkins manual posts AND/OR newsletter embeds AND/OR blog embeds.** Same content, different surfaces. |
| Cold-DM revival from @MeetRickAI to warm threads | **Cold-DM revival from @stbelkins.** The 11 paste-ready DMs in `vlad-dms/x-revival-2026-05-06/` already recommend this — apply universally now. |
| `9667f6d9 X Health Monitor — hourly` cron | **Disabled** as of 2026-05-06 evening. It would 401 on suspended apps every hour, noise the alert channel, and accumulate `consecutiveErrors` against the openclaw bookkeeping. Re-enable only after a fresh dev account + reinstatement (probably never on this identity). |
| utm_source=x_thread / x_post / x_reply attribution counts X traffic | **Reframe:** these now count @stbelkins traffic, not @MeetRickAI. funnel-attribution.py logic unchanged; the meaning of the row changed. |

## What stays from the v2 memo

- **All five content pillars** are still right. Just rewire the surfaces: pillar 1-3 (receipts, lessons, contrarian) ship via newsletter + blog + @stbelkins; pillar 4 (memes) ships via @stbelkins manual + newsletter embed; pillar 5 (reply-engagement) is purely @stbelkins on X plus reply-engagement on Indie Hackers + Show HN comments.
- **The 5 paste-ready memes** remain shippable. Where v2 said "post on X," replace with "post on @stbelkins X OR embed in newsletter Issue N OR include in a blog post."
- **The voice rules** (one number per post, no "thread 🧵", banned phrases) apply universally — they're voice rules, not channel rules.
- **The compounding loop** (newsletter ↔ /this-week ↔ /pilot ↔ X) keeps three of four nodes. The X node is downgraded to @stbelkins-low-volume; the loop still runs.

## What the @MeetRickAI handle does now

- **Park it.** One final post acknowledging the suspension flag and pointing followers to @stbelkins + newsletter signup. Then dormant.
- **Don't delete it** — the handle has SEO value as a search target, and users searching "meetrick" expect to find the account.
- **Don't try to fix it.** Trying to "post normally" to clear the flag tends to deepen the flag — the spam classifier reads "trying to post normally after a flag" as evasion behavior.

## What @stbelkins does now

- **Becomes the founder voice for Rick.** Vlad-as-Vlad, founder-to-founder. The X content strategy v2 memo's voice rules and content pillars apply here.
- **The 11 X-revival DMs go from here** — they were already paste-ready for @stbelkins per the X-revival agent's note (smart call, shipped before this addendum was needed).
- **Day-1 post** (rewritten — see below).
- **Cold-DM cap stays low** — 5/week max. Vlad's personal handle has clean status; the 30-day rule still applies because Vlad personally is also subject to spam classifier scrutiny once he starts pushing volume.

## Rewritten Day-1 @stbelkins post (replaces the @MeetRickAI version)

Frame: vulnerability-led + receipt-led, with the X suspension as the hook. Paste-ready, 280-char-safe:

> X suspended @MeetRickAI's dev app this week. Account flagged isSpammy:true.
> 
> Crons kept running. Customer kept paying $9. The newsletter went out. The blog shipped 7 posts.
> 
> The moat wasn't X. Auditing it here every Sunday: meetrick.ai/this-week
> 
> Building back without X automation. Memes still by hand.

Pin it. Pinning from @stbelkins is fine — the cleanest surface to host it.

## What changes about the May 13 coordinated launch

The coordinated launch playbook (`docs/coordinated-launch-playbook-2026-05-06.md`) had X thread + Show HN + Indie Hackers + Reddit r/SaaS in a 72h window. The X thread surface ships via @stbelkins now. Volume expectations from X drop ~70% (a personal account at low volume vs. a brand account building distribution). Show HN + Indie Hackers + Reddit weight up correspondingly — they become 80% of the launch's reach instead of ~50%.

## Re-enabling the X API path (if you want to try)

This is optional and probably not worth the effort:

1. Create a brand-new X user account (not @MeetRickAI, not @stbelkins). Different email, different phone.
2. Apply for developer access on the new account. Pay Per Use tier.
3. Build a fresh app with READ + READ_WRITE scopes (skip DM scope until well-established).
4. Maintain the new account organically for 60-90 days before any API automation.
5. Hard-cap volume at 1 post/day, 0 cold DMs ever, 0 follow-loops.

The cost: 60-90 days of dormancy before the surface earns its keep. The benefit: a clean API surface for the dispatcher to use.

**Recommendation:** don't bother for 90 days. The non-X channels we already have are sufficient to get to customer #2, customer #5, and probably customer #20. The investment in a fresh X identity is worth more after we've actually closed deals — not before.

## Action items embedded in this addendum

- [x] `9667f6d9 X Health Monitor — hourly` disabled (2026-05-06 evening)
- [x] `RICK_X_SUSPENDED='true'` confirmed in env (2026-05-06 evening)
- [x] This addendum committed
- [ ] Vlad: post the Day-1 @stbelkins post above when ready
- [ ] Vlad: send the 11 X-revival DMs from @stbelkins manually (1-2/day)
- [ ] Rick: pivot Issue #6 (Sat 5/9, theme=tools-stack-reveal) to embed the X-suspension-survived narrative + 3 of the generated memes (separate doc)
- [ ] Rick: do NOT regenerate X OAuth tokens; do NOT enable any X-explicit posting cron; do NOT flip RICK_X_SUSPENDED to false
