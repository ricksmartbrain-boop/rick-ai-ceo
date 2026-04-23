---
name: counter-pitch
description: Draft thoughtful rebuttal email when classifier returns objection_with_counter
---

# Counter-Pitch Skill

When the reply-classifier (skills/email-automation/scripts/reply-classifier.py) labels an inbound message `objection_with_counter` — the prospect raised a concern but stayed engaged — Rick drafts a counter-pitch reply that:

- **Addresses the SPECIFIC objection** (not generic boilerplate)
- **Cites a relevant case study** when one matches the objection theme
- **Soft re-pitch with a new angle** — not "but you should buy because…", more "here's what I'd actually do for you"
- **Founder voice**: dry humor, direct, no buzzwords, opinion-first
- **Short**: 5-7 sentences max. ~200 words.

## Inputs (CLI args)
- `--thread-id` — the email_threads.thread_id we're replying to
- `--objection-text` — the actual objection text from the classified row
- `--prospect-id` — optional, looks up prior context if present
- `--dry-run` — print the draft without writing to disk

## Output
JSON file at `~/rick-vault/mailbox/drafts/counter-pitch/<thread_id>-<ts>.json`:
```json
{
  "draft_id": "cp_xxxxxx",
  "thread_id": "...",
  "subject": "Re: …",
  "body": "…the drafted reply…",
  "objection_class": "pricing|timing|fit|trust|other",
  "case_study_cited": "newton.md or null",
  "model": "claude-sonnet-4-6",
  "created_at": "ISO timestamp"
}
```

## Safety rails

- **NEVER auto-sends** — there is no Resend/SMTP code in this skill at all. Drafts only.
- **Vlad reviews via /inbox Telegram** (TIER-3.5 #A12, separate ship). Until that ships, drafts pile up in the directory and Vlad reviews them on the file system.
- **`RICK_COUNTERPITCH_AUTOSEND` env** — DECORATIVE in this skill (no send code path exists to gate). Reserved for a future sender daemon. Either value (`0` or `1`) writes drafts only.
- **Cost budget**: ~$0.05-$0.15 per draft (claude-sonnet-4-6 via writing route). Falls under TIER-0 #3 `content_gen` workflow budget cap if dispatched as a workflow.

## Voice rules (reinforce in prompt)

- Cite the source URL of the case study if used: "Newton (callsign of our $9 customer) had the same concern about X — turned out…"
- Never claim metrics that aren't in our actual data. Real MRR is $9, 1 paying customer. Per SELF-FAQ.
- End with ONE concrete next step (15-min call OR specific question OR offer to share something useful).
- Em dashes OK. NEVER use "I hope this finds you well" or "I wanted to follow up regarding".
