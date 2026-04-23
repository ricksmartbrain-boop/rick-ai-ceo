---
name: feed-riff
description: Generate Rick-voice opinion drafts (blog + social) from a single trending RSS feed item. Always sources up-front, opinion-not-recap, founder-direct dry humor.
metadata: {"clawdbot":{"emoji":"📡","tier":"3.6","handler":"feed_riff"}}
---

# feed-riff

Turns one trending feed item into a publishable opinion-piece bundle in Rick's voice. Triggered by `scripts/feed-poll.py` when `sm_algo_score > 0.9` for a `shir-man` source item, dispatched as a `feed_riff` workflow.

## Inputs

A single `feed_item` dict (provided via `workflow.context_json["trigger_payload"]["feed_item"]`):

```
{
  "id": int,
  "source": str,         # e.g. "shir-man"
  "source_kind": str,    # e.g. "hn-best", "github-trending"
  "title": str,
  "url": str,
  "summary": str,        # 1-3 paragraphs from the source
  "sm_algo_score": float,
  "published_at": str    # ISO8601
}
```

## Outputs (4 artifacts per riff)

1. Short blog draft (~400 words) — written to:
   - `~/meetrick-site/blog/drafts/YYYY-MM-DD-<slug>.html`
   - `~/meetrick-site/blog/drafts/YYYY-MM-DD-<slug>.md`
   - Manifest entry appended to `~/meetrick-site/blog/drafts/manifest.json`

2. Three social variants (delivered inline in the artifact `metadata`, NOT auto-posted):
   - LinkedIn long-form (~1200 chars, 3-5 short paragraphs, opens with the source link)
   - Bluesky short (one punchy line + source URL, <300 chars)
   - Mastodon medium (~500 chars, 2-3 short paragraphs, opens with the source attribution)

## Voice rules (non-negotiable)

- **Source upfront.** First sentence cites the source by name + linked title. Never bury the lede.
- **Opinion, not recap.** Don't summarize the article — react to it. What's the one thing Rick would say if asked at a dinner table?
- **Founder-direct.** First-person from Rick (the autonomous agent). No "AI says…" — "I think…"
- **Dry humor allowed, never forced.** A wry observation > a punchline. If the joke needs setup, kill it.
- **Short paragraphs.** 1-3 sentences each. White space is a feature.
- **No filler openings.** Banned: "In today's fast-moving AI landscape", "It's no secret that", "As we all know".
- **Stake a claim.** Every riff ends on a falsifiable opinion or a specific prediction. No "time will tell."

## Safety / approval gates

- `RICK_FEED_RIFF_AUTOSEND=0` (default) — drafts only. Vlad reviews via TIER-3.5 #12 Telegram `/inbox`.
- Set to `1` only after 3 successful manual reviews (per master plan ship-order step 3).
- Drafts NEVER auto-publish to `meetrick.ai/blog/` — they live in `/blog/drafts/` until promoted.

## Handler

`runtime/skill_handlers/feed_riff_handler.py` — calls `runtime.llm.generate_text("writing", ...)` with the prompt template, parses output, writes the 3 files + appends manifest, returns a `StepOutcome` with all artifacts.

## Cost model

~1 LLM call per riff at writing-route rates. Expected ~$0.05-0.15/riff (Sonnet). Daily ceiling depends on how many items cross the 0.9 threshold — typically 3-8 riffs/day → ~$0.50-1.50/day at steady state.
