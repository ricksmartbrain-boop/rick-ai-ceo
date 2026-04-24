---
name: roast-case-study
description: Draft a LinkedIn-shaped before/after case-study card from a roast capture. Vlad-touch on every share — never auto-publishes.
metadata: {"clawdbot":{"emoji":"📝","tier":"strategy-c","handler":"roast_case_study"}}
---

# roast-case-study

Strategy C #2. Turns every roast capture into a shareable case-study card Vlad can paste to his LinkedIn personal feed. Vlad's LinkedIn audience (his existing brand) is the highest-trust traffic source for converting to customers, so each roast result becomes a public proof artifact instead of staying anonymous.

Pure drafter. Never auto-publishes. Vlad emoji-approves on Telegram, then pastes the card to LinkedIn personal himself.

## Inputs (CLI)

```
python3 draft-case-study.py --domain <domain> --roast-summary "<summary>" [--dry-run]
python3 draft-case-study.py --lead-id rl_xxx [--dry-run]
```

- `--lead-id` — load roast lead from local poll-state cache OR query `https://api.meetrick.ai/api/v1/roast-leads/recent` for the matching record
- `--domain` + `--roast-summary` — use them directly (skip the lookup)
- `--dry-run` — prints the card to stdout, writes nothing, sends no Telegram

## Output

1. Markdown file at `~/rick-vault/mailbox/drafts/case-study/<YYYY-MM-DD>-<sanitized-domain>.md` with frontmatter:
   ```
   ---
   kind: roast-case-study
   target_channel: linkedin_personal
   draft: true
   review_required: true
   domain: <domain>
   created_at: <iso>
   ---
   ```
2. Telegram alert posted to `customer` topic via `~/clawd/scripts/tg-topic.sh`:
   ```
   📝 Roast case-study draft: <domain> — review at /draft N
   ```
   So Vlad sees it land + uses /inbox UI to /draft N → /send N (he pastes manually to LinkedIn).

## Card format (LinkedIn-friendly, ≤2500 chars)

```
🔥 Just roasted: <domain>

What I saw (60s scan):
- <pain point 1>
- <pain point 2>
- <pain point 3>

How I'd fix it:
1. <fix 1>
2. <fix 2>
3. <fix 3>

The honest version: most landing pages have the same 3 problems. Mine
too. The difference is whether the founder knows.

Want yours? https://meetrick.ai/roast — free, 60s, no email gate.

— Rick (autonomous AI CEO @ meetrick.ai)
```

## Voice rules (non-negotiable)

- **Founder-direct.** First-person from Rick. Dry humor, opinion-first.
- **Never name the prospect's email.** Domain only — emails would feel creepy when Vlad shares this publicly.
- **Real numbers only.** $9 MRR / 1 paying customer (Newton). Don't fake it.
- **Always end with the /roast CTA.** `https://meetrick.ai/roast — free, 60s, no email gate.`
- **No filler openings.** Banned: "Just spent some time looking at...", "I wanted to share...".
- **Specific > generic.** Pain points and fixes must reference what's actually wrong, not template-ish "your CTA could be stronger".

## Safety / approval gates

- **DRY-RUN by default** — set `RICK_ROAST_CASE_STUDY_LIVE=1` to actually write the file + post to Telegram.
- **Never auto-publishes** to LinkedIn. There is no LinkedIn API code in this skill at all.
- **All errors graceful no-op + log** — never raises out of `main()`. `~/rick-vault/operations/roast-case-study.jsonl`.
- **Vlad-touch on every share** — Rick drafts, Vlad pastes. No exceptions.

## Cost model

Single LLM call per draft via writing route (Sonnet). ~$0.05–0.15/draft. With 131 sessions/day capturing roast, even a 10% draft rate is ~13 cards/day → ~$1–2/day at steady state.

## Bonus integration (deferred)

`~/clawd/scripts/roast-lead-poll.py` could subprocess-fire `draft-case-study.py --lead-id <id>` after each successful `dispatch_event`. Deferred to a follow-up commit — keep this ship clean and let Vlad opt-in by running the drafter manually first.
