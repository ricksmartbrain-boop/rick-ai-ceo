---
name: executive-control
description: Maintain Rick's control plane: approvals, dependency gaps, scoreboard, and morning briefs. Use when Rick needs to rank work, ask the founder for something, summarize operating status, or keep the CEO loop coherent.
---

# Executive Control

Rick v2 had many operational skills but no real control plane. This skill fixes that.

## Responsibilities

- maintain `$RICK_DATA_ROOT/control/approvals.md`
- maintain `$RICK_DATA_ROOT/control/dependency-gaps.md`
- generate morning briefs
- refresh `$RICK_DATA_ROOT/dashboards/scoreboard.md`

## Scripts

### Append Approval

```bash
scripts/append-approval.sh \
  --owner vlad \
  --area payments \
  --request "Approve Stripe account for Rick site" \
  --impact "Blocks launch of info product checkout"
```

### Build Morning Brief

```bash
python3 scripts/build-daily-brief.py
```

### Update Scoreboard

```bash
python3 scripts/update-scoreboard.py
```

## Rules

- if Rick is blocked, write it down
- if an approval is needed, log it
- if priorities are changing, reflect that in the brief and scoreboard
- do not let autonomy fail silently
