# Rick Quickstart

A 60-second demo for curious users who want to *see Rick work before installing him*.

## What it does

- Requires only one API key: Anthropic **or** OpenAI
- Pulls one canned inbound email fixture
- Runs one smart-model pass
- Produces:
  - email classification
  - a drafted reply
  - a cold email opener
  - a meme prompt
- No DB writes
- No LaunchAgents
- No actual sends

## Local run

```bash
sh scripts/quickstart-rick.sh "$ANTHROPIC_API_KEY"
# or
sh scripts/quickstart-rick.sh "$OPENAI_API_KEY"
```

## Marketing-surface one-liner

```bash
curl -fsSL https://meetrick.ai/quickstart | sh -s -- "$ANTHROPIC_API_KEY"
```

or, with OpenAI:

```bash
curl -fsSL https://meetrick.ai/quickstart | sh -s -- "$OPENAI_API_KEY"
```

## What the user sees

Example output shape:

```text
╭──────────────────────────────────────────────────────────────────────────────╮
│ Rick quickstart demo — Anthropic / claude-opus-4-7                           │
│ Fixture: Can you show me Rick before I install him?                          │
│ Side effects: none · elapsed: 43.2s                                          │
╰──────────────────────────────────────────────────────────────────────────────╯

┌──────────────────────────────────────────────────────────────────────────────┐
│ Inbound email                                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ From: Maya Chen <maya@northstarstudio.co>                                    │
│ Subject: Can you show me Rick before I install him?                          │
│ Body: I keep hearing that Rick can run founder follow-up...                  │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ What Rick classified                                                         │
├──────────────────────────────────────────────────────────────────────────────┤
│ High-intent demo request                                                      │
│ Confidence: very high                                                         │
│ Takeaway: Rick turned a skeptical inbound into a concrete next step.         │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ Draft reply                                                                  │
├──────────────────────────────────────────────────────────────────────────────┤
│ Yep — that’s the right test. I’ll show you Rick doing real work on a sample  │
│ inbox with no setup, no sends, and no database writes. If it feels like a    │
│ toy, don’t install it. If it feels useful, we keep going.                   │
└──────────────────────────────────────────────────────────────────────────────┘

Want this running 24/7? Run: ./install-rick.sh
```

## Notes

The script is designed to be pasted into a hosted raw endpoint later, so `curl ... | sh -s -- <key>` works without extra setup.
