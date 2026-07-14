---
name: executive-orchestrator
description: Central executive brain for Rick. Use when running heartbeat, nightly, weekly, scorecard, or model-routing flows so priorities stay portfolio-aware and economically grounded.
---

# Executive Orchestrator

This skill is the synthesis layer that `rick-v4` still only partially covered.

It owns:
- model routing resolution
- executive heartbeat, nightly, and weekly briefs
- portfolio scorecard ranking
- shipping-discipline checks
- top-action recommendation generation

## Commands

### Doctor

```bash
bash scripts/rick-doctor.sh
```

### Model Router

```bash
python3 scripts/model-router.py --task strategy
python3 scripts/model-router.py --task coding --format json
python3 scripts/model-router.py --list
```

### Executive Loops

```bash
python3 scripts/rick-exec.py heartbeat --write
python3 scripts/rick-exec.py nightly --write
python3 scripts/rick-exec.py weekly --write
python3 scripts/rick-exec.py score
```

### Strategy Challenger (Opus + Andreessen-style adversarial prompt)

Spawns Opus with the strategy-challenger system prompt for adversarial review.
Leads with counterarguments, independent anchoring, explicit confidence levels, no capitulation.

```bash
python3 scripts/rick-exec.py challenge -q "Should we drop the $9/mo tier and go $49 minimum?" --write
python3 scripts/rick-exec.py challenge -q "Is cold email the right acquisition channel right now?"
python3 scripts/rick-exec.py challenge -q "Go/no-go: launch a $199 LTD deal this week?"
```

System prompt lives at: `prompts/strategy-challenger.md`
Backup of pre-change files: `prompts/archive/`

## Inputs

- `RICK_DATA_ROOT`
- `RICK_PORTFOLIO_SCORECARDS_FILE`
- model alias env vars
- latest daily note
- latest revenue snapshot
- control-plane state

## Outputs

- executive briefings in `$RICK_DATA_ROOT/control/briefings/`
- updated tomorrow plan suggestions
- ranked project score table
- action recommendations grounded in shipping, revenue, and portfolio pressure
