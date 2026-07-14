# Execution Ledger

Use this skill to make Rick's autonomy auditable.

What it does:
- records important operating events into a JSONL ledger
- summarizes recent execution into a dashboard
- keeps decisions, runs, blockers, and launches queryable later

Primary commands:

```bash
python3 skills/execution-ledger/scripts/execution-ledger.py record \
  --kind decision \
  --title "Paused low-score project" \
  --status done \
  --area portfolio \
  --project personal-brand \
  --notes "Weekly scorecard review pushed capital toward higher-conviction work."

python3 skills/execution-ledger/scripts/execution-ledger.py summary --write
```

Use it when:
- Rick ships something important
- a strategy decision changes priorities
- a blocker or approval request matters
- you want a readable audit trail of what autonomy actually did
