# X Content Strategy v2 — OUTSTANDING (60-Day Compounding Arc)

**Anchor:** 2026-05-06. Extends `docs/x-content-strategy-2026-05-06.md`, does not replace.
**Constraints:** zero automation on posts/DMs/replies/follows for 30 days. 1-2 posts/day. Smart-models invariant.
**STATUS UPDATE 2026-05-06 evening:** X Developer App "Rick AI App" (id 32569332) currently SUSPENDED at developer.x.com. @MeetRickAI user account is fine; manual posting via X web/mobile is the only viable path until the dev-app appeal resolves. This memo's manual-only design fits the constraint exactly — no API automation was assumed in the first place.

---

## TL;DR

The 30-day operator-paced re-entry is the safety floor; this v2 extends it to 60 days, where days 1-30 prove "humble + receipts" doesn't get re-suspended and days 31-60 compound that credibility into 10x reach via memes-as-content-pillar, contrarian threads tied to Issue #5/#7, and a reply-engine playbook. Memes ship 1-2/wk max (operator-only — Memelord generates, Vlad picks + posts via web), gated to events that already deserve a meme. Five content pillars, a single chosen "we're back" variant (vulnerability-led with a number anchor), and 5 paste-ready memes pre-loaded for the May 6-31 window. By day 60 the funnel-attribution `utm_source=x_*` rows should be the second-largest non-direct source after newsletter, and we lose zero distribution to a re-suspension.

---

## PART 0 — What's already in place

- **Operator-paced re-entry memo** (`docs/x-content-strategy-2026-05-06.md`) — 30-day spine, day-1 post, weekly thread, reply-guy ratio. v2 inherits all.
- **Memelord stack** (live): `https://www.memelord.com/api/v1/ai-meme` (1 credit/image), `runtime/media_factory.py::_memelord_image()`, `scripts/memelord-pipeline.py` (mine for prompt patterns; do NOT auto-run for X). 11 prior meme drafts on disk for picks.
- **Drafts in tree:** `~/rick-vault/projects/x-drafts/this-week-2026-05-04-thread.txt`, `~/rick-vault/projects/x-drafts/newsletter-issue-005-2026-05-05-x-thread.txt` — both ready for manual paste.
- **30-DM batch:** 9 of 30 are X-channel (Marc Lou, skwee357, Caleb Porzio, Tony Dinh, Andrey Azimov, etc.) — DO NOT cold-DM until recipient surfaces (replied, liked, follows). Cold-DM is the most likely re-suspension trigger.
- **UTM convention:** `utm_source=x_thread`, `x_post`, `x_reply`. `funnel-attribution.py` keys off this.
- **No `xpost-state` / `x-mentions` / `x-dm-history` files** in `~/rick-vault/operations/` today. Day 1 starts with a clean log; Vlad hand-edits `x-dm-history.jsonl` after each manual DM.

---

## PART A — The 60-Day Arc

### Days 1-30 (5/6 → 6/4): Operator-Paced Re-Entry
Already specified. Own the suspension once on day 1, ship 1-2 posts/day with at least one number per post, post the Saturday `/this-week` thread, let `/pilot` be the only CTA.

### Days 31-60 (6/5 → 7/4): Compound Phase

| Lever | Days 1-30 | Days 31-60 |
|---|---|---|
| Posts/day | 1-2 | 2-3 |
| Replies/day | 5-10 | 10-20 |
| Threads/week | 1 (Sat `/this-week`) | 2 (add Tue newsletter recut) |
| Memes/week | 0-1 | 1-2 |
| Cold DMs/week | 0 | 5-10, ONLY to people who've engaged ≥2x |
| Quote-tweets | 0 | 2-3/week (warm QT, never dunks) |
| Pinned post | "We're back" announcement | Best-performing thread from days 1-30 |

**Day-60 success bar:**
- ≥10x impressions per post vs. day-1 baseline
- ≥1 customer-#2 reply traceable to `utm_source=x_*`
- `x_thread` + `x_post` + `x_reply` non-zero in funnel-attribution rollup
- Reply ratio: ≥30% trigger follow-back from original poster
- Newsletter sub source mix: X = ≥15% of new subs (today: 0%)

If by day 45 the X-attributed pipeline is still flat, the lever is positioning re-think (mirror Issue #5: "narrow before scale"), not more posts.

---

## PART B — Content Pillars (5, with weekly ratio)

The mix is operator-paced; weekly counts assume 12-15 original posts/week (1-2/day × 7).

| # | Pillar | Days 1-30 % | Days 31-60 % | Why |
|---|---|---|---|---|
| 1 | **Receipts** (numbers, screenshots, live state) | **40%** | **30%** | The frame Rick exists in. Without numbers, this account is theater. |
| 2 | **Lessons** (what broke, fixed, surprised — past tense, specific) | **25%** | **25%** | High-trust signal. "I broke X" reads as competence, not weakness. |
| 3 | **Contrarian takes** (positions earned via receipts) | **15%** | **20%** | The follow-trigger. Receipts get likes; takes get follows. |
| 4 | **Memes** (whimsy WITH substance) | **5%** | **10%** | Distribution multiplier. Cap at 10% or account drifts to meme-account. |
| 5 | **Reply-engagement** (reply-amplified observations) | **15%** | **15%** | Replies are 3:1 to posts in raw count, but only ~15% of *originals* are reply-amplified moments. |

Hard rule: every post fits exactly one pillar. If it fits two, it's two posts.

---

## PART C — Memes as a Content Pillar

### C.1 Why memes work for THIS account

The "Rick at $9 MRR running on his own product" frame already IS a meme. The suspension scar is a meme. The 9-cancellations is a meme. We're caption-fitting receipts into formats the build-in-public crowd already shares.

Risk: meme-account drift. Mitigation: cap at 1-2/week, every meme references a real number. No generic "founder life" memes.

### C.2 Meme types that fit

1. **Receipt-meme** — meme format + real metric overlaid. (Drake panel — "Drake rejecting: scaling at $9 MRR / Drake approving: pausing cold-email at 5% bounce".)
2. **Vulnerability meme** — softens an admission. ("This Is Fine" + "9 cancellations in 14 days but the breakup lines were free market research".)
3. **Contrarian meme** — visualizes a take that opens DMs. (Galaxy-brain — "Add channels / Cut channels / Ship same channel 90 days / Ship same channel 90 days while writing about it".)
4. **Status-update meme** — milestone without marketing. (Achievement-unlocked — "Survived an X suspension. Came back manual.")
5. **NEVER ship:** generic founder-grind memes, AI-doomer memes, dunks on competitor agents, "humans bad / AI good" framings.

### C.3 Meme cadence

- **Days 1-7:** ZERO memes. Re-entry must be receipts, not jokes.
- **Days 8-30:** 1 meme/week, only Sat or Tue (synced to `/this-week` or newsletter day). The meme supports a real post — never standalone.
- **Days 31-60:** 1-2 memes/week. Second slot is reactive (a real moment that day deserved one).
- **Hard ceiling:** 2/week ever.

### C.4 Meme triggers

YES: a run of cancellations, a funnel row that flipped, a public post by an ICP founder Rick can warmly QT, a `/this-week` thread with a strong number, a product-truth that would otherwise sound like humblebrag.

NO: feature shipped, sub milestone, trending topic with no Rick angle, reply burn, "it's been a week."

### C.5 Operator workflow (manual, 30+ days)

1. Vlad opens this memo on a meme-trigger day.
2. Picks a format from C.2, writes a one-line trigger.
3. Runs `python3 ~/.openclaw/workspace/scripts/memelord-pipeline.py --dry-run --count 3` with custom prompt OR pulls from the 11 pre-rendered files on disk.
4. Memelord returns 1-3 image URLs → `/tmp/memes/` or `~/rick-vault/media/`.
5. Vlad picks ONE manually. Types caption manually. **Posts manually from X web/mobile** (not via API — dev app currently suspended anyway). Adds post URL + meme path to `~/rick-vault/operations/x-dm-history.jsonl`.

### C.6 Five paste-ready memes for May 6-31

Each: `[trigger] | [format] | [caption] | [Memelord prompt]`.

**Meme 1 — "We survived the timeout" (post day 7, after first weekly thread)**
- Trigger: end of week 1, no re-suspension.
- Format: achievement-unlocked badge.
- Caption: "7 days back. 14 posts. 0 strikes. 1 reply that turned into a DM. The slow lane is fine."
- Prompt: `Achievement unlocked screen, retro game style, badge title "Re-Entry Day 7", subtitle "0 strikes, 1 warm DM", clean monochrome with one accent color, no other text overlay`

**Meme 2 — "Narrow before scale" (post Tue 5/12, paired with newsletter Issue #5 thread)**
- Trigger: newsletter Issue #5 publishes; X thread already drafted.
- Format: galaxy-brain (4 panels, escalating absurdity).
- Caption: "Most founders are scaling the wrong thing in AI. Stages of distribution enlightenment ↓"
- Prompt: `Galaxy brain meme, four panels increasing brain glow. Panel 1: "Add another channel". Panel 2: "Add five more channels". Panel 3: "Cut every channel that's not working". Panel 4: "Cut one channel before adding one — and write down who it was for". Clean comic-book inks, no extra text outside the panel labels`

**Meme 3 — "9 cancellations" (post 5/15-5/16 if Issue #5 thread gets traction; else hold)**
- Trigger: indie hacker quote-replies asking how the 9 cancellations went.
- Format: This Is Fine dog, single panel.
- Caption: "Got 9 cancellations in 14 days. Read every breakup line. Best market research I've done since launching."
- Prompt: `Classic "This is fine" dog meme but the room has nine paper printouts pinned to the walls labeled "cancellation 1" through "cancellation 9". The dog is calmly highlighting one with a marker. Single panel, comic style, no caption text in the image — leave caption space empty`

**Meme 4 — "$9 MRR vs the build-in-public discourse" (day 21-25, after a contrarian thread)**
- Trigger: reply burst on a viral indie-hacker MRR-brag thread.
- Format: distracted boyfriend.
- Caption: "Most of the timeline rn / Boyfriend: indie hackers / Girlfriend: '6-figure side project in 30 days' / Other woman: '$9 MRR, 50 days, here's what's actually broken'"
- Prompt: `Distracted boyfriend meme, three labels. Boyfriend: "indie hackers". Girlfriend (ignored): "$9 MRR receipts thread". Other woman: "6-figure side project in 30 days". Clean photo style, labels readable, no other text`

**Meme 5 — "Customer #2 fork" (post the day Arjun replies one way or other, ~5/10-5/12)**
- Format A (yes): two-buttons sweat. Caption: "Customer #2 said yes. Now I have to onboard a real human in real-time. Two buttons: ship the playbook I wrote / panic and rewrite the playbook. (I picked panic.)"
- Format B (no): Drake. Caption: "Customer #2 said no. Drake rejecting: 'pivot'. Drake approving: 'send the breakup line back as a thank-you and ask what would've made it a yes.' Issue #6 incoming."

---

## PART D — Top 5 Content Formats

For each: **example | why | when | NOT**.

### Format 1 — Before/after data screenshot
- **Ex:** Bounce dashboard before/after — "Before: 6%. After: 0.4%."
- **Why:** The screenshot IS the source.
- **When:** ≥1/week. Sat or Tue.
- **NOT:** screenshots without a number annotation. LLM-chat screenshots.

### Format 2 — Single-tweet contrarian take
- **Ex:** "Cold email at $0 MRR is positioning research disguised as a channel."
- **Why:** Earns a follow without a thread. Replies become DM funnel.
- **When:** 1-2/week. Mid-morning PT.
- **NOT:** takes Rick hasn't earned with receipts. Anti-AI / anti-competitor takes.

### Format 3 — Vulnerable failure post
- **Ex:** "Customer #2 told me the pricing felt 'agency-cheap'. Raised pilot tier $499 → $999. He bought. Lesson cost 3 weeks."
- **Why:** Build-in-public allergic to polish. Failure-with-fix highest-leverage.
- **When:** ≥1/week. Day failure is fresh enough to feel mild discomfort.
- **NOT:** failures without fix/learning. Pity-posting. Fake-vulnerability.

### Format 4 — Watch-me-ship-real-time thread
- **Ex:** Saturday `/this-week` thread (already drafted). Hook = number. Body = 5-7 specifics. Final = `/this-week` link with utm.
- **Why:** Weekly cadence trains audience to expect Saturday content.
- **When:** Every Saturday 9am PT. Add Tue 9am PT thread on day 31.
- **NOT:** threads without final-tweet CTA. "Thread incoming" tweets without queued thread.

### Format 5 — Meme that says what 1000 words can't
- **Ex:** Meme 2 above (galaxy-brain "narrow before scale"). Same 600-word newsletter argument compressed to 4 panels.
- **Why:** Build-in-public crowd's QT currency.
- **When:** Per Part C.3 cadence.
- **NOT:** memes without number anchor. Generic founder memes.

---

## PART E — Voice Rules (7 hard rules)

1. **One specific number per post is the floor.** No number → either skip or surface one from prod logs.
2. **No emoji-only replies.** "🔥", "👏", "💯" = low-effort.
3. **Banned phrases:** "thread 🧵", "this is a banger", "literally", "unhinged", "absolutely cooking", "a quick thread on", "let me cook", "btw I just shipped", "drop a 🤝 if". X-norm has antibodies.
4. **No "founder life" generalities.** Replace generic with specific.
5. **Every link is UTM'd.** `utm_source=x_thread|x_post|x_reply`, `utm_medium=distribution`, `utm_campaign=` (one of: `this-week-share`, `issue-N`, `pilot-cta`, `manual-reply`).
6. **No more than one CTA per post.**
7. **Pass the "Vlad-DM-to-founder-friend" test.** Press release = wrong. Slack at 11pm = right.

---

## PART F — The Compounding Loop

| Surface | X feeds (a) | X closes (b) | X samples (c) |
|---|---|---|---|
| Newsletter (Tue/Sat) | Tue 9am thread = recut of morning issue. UTM `x_thread`, campaign `issue-N`. | Final tweet links to newsletter signup, NOT pilot. | Quote from prior issue as single-tweet take mid-week. |
| `/this-week` (Sat 9am) | Sat 9am thread = recut. UTM `x_thread`, campaign `this-week-share`. | Weekday post linking to last Sat's `/this-week` when number lands new. | Most-changed line of `/this-week` as Tue morning teaser. |
| `/pilot` | One post/week max. Contrarian-take post drops pilot link in *reply* in same thread, not OP. UTM `x_post`, campaign `pilot-cta`. | Sat weekly thread final tweet → `/pilot` only if week's narrative was about onboarding. | Reply-thread: when ICP founder asks "free trial?", drop `/pilot` as reply. UTM `x_reply`. |

Rule: never feed AND close in same tweet. OP sets hook; reply or final-tweet closes.

---

## PART G — The "We're Back" Announcement Post

See companion memo `docs/x-announcement-post-2026-05-06.md` (commit `065db38`) for full 3-variant analysis + 5 paste-ready replies + 48h plan. Recommended winner: **Frame 2 (Receipts-First)** — anchors `/this-week` as Day-1 brand primitive without making suspension the protagonist.

Pin the post. Leave pinned 7-14 days, replace with highest-engagement Sat thread on the 14th if it earns the spot.

---

## PART H — Suspension-Risk Minimization (HARD CONSTRAINTS)

Inherits everything from `x-content-strategy-2026-05-06.md` PART F. Additions:

1. **Memelord is fine; Memelord-to-X is not.** Generation API is OK; X-side is the only side that has to stay manual.
2. **No follow/unfollow loops.** ≤5 follows/day, ICP only. Never unfollow non-followers as strategy.
3. **No link shorteners.** Raw `meetrick.ai/...?utm_source=x_*` only. Bit.ly trips spam classifier on small accounts.
4. **No identical text twice.** Every post hand-typed.
5. **Cold-DM ban for 30 days, conditional after.** Days 1-30: zero. Days 31-60: ≤5/week, ONLY recipients who engaged ≥2x.
6. **Suspension hypothesis (no pre-suspension logs found):** likely (a) cold-DM volume from any tool authenticated as Rick, (b) repeated identical/similar message text, (c) external-link cadence above small-account threshold. v2 prevents all three by construction.
7. **If a post draws unusual reply burst (>50 in <30min):** Vlad pauses replying for 1h. Reply-bursts can look like a raid to X's spam classifier.
8. **One canary per Saturday.** Vlad checks X dev settings + sent-DMs surface every Saturday before posting weekly thread.

---

## PART I — Days 31-60 Plan-Beyond-Plan

- **Week 5 (6/5 → 6/11):** Add Tue 9am newsletter-recut thread. First meme-of-the-week (Meme 1 if not yet shipped). First 5 cold DMs of conditional batch — only to recipients who engaged twice.
- **Week 6 (6/12 → 6/18):** First "lessons from week-5 cold DMs" post. Second meme — must be reactive. Begin reply-tracking in `~/rick-vault/operations/x-dm-history.jsonl`.
- **Week 7 (6/19 → 6/25):** First contrarian thread that's not a newsletter recut. Pin if it lands. First QT of an ICP founder (warm only — adding a number, not dunking).
- **Week 8 (6/26 → 7/4):** "60 days back" Sat thread. Six numbers, a chart, next 60-day commitment. If `utm_source=x_*` is second-largest non-direct row, strategy worked. If not, day-60 thread is post-mortem and next 30 days narrow to one pillar.

---

## PART J — Open Questions

1. **Day-7 weekly thread (5/12) — call out Customer #2 by name (Arjun) if fork closes yes?** Recommendation: only with explicit consent in DM.
2. **Does `funnel-attribution.py` aggregate `x_post` + `x_thread` + `x_reply` into one X row, or three?** Verify before day-60 measurement.
3. **`x-dm-history.jsonl` — proactive or lazy-create?** Recommendation: lazy on first DM.
4. **DEV APP appeal status (NEW 2026-05-06):** "Rick AI App" SUSPENDED at developer.x.com. Pay Per Use tier. Vlad needs to file appeal in Developer Portal Settings. Until reinstated, all X API calls return 401 — manual posting via web/mobile is the only viable path. This memo's design fits exactly (operator-paced manual). Resume Memelord-to-clipboard-to-X-web workflow.
