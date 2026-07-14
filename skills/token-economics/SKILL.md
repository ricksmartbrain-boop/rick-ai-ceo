# Token Economics

Use this skill to turn model spend into an operating metric.

What it does:
- records LLM usage events with cost and token counts
- compares spend against Rick's daily route caps
- writes a dashboard for budget pressure and model mix

Primary commands:

```bash
python3 skills/token-economics/scripts/token-usage.py record \
  --bucket strategic \
  --provider openai \
  --model gpt-5.4 \
  --usd 2.41 \
  --input-tokens 18234 \
  --output-tokens 4132 \
  --task "weekly synthesis"

python3 skills/token-economics/scripts/token-usage.py report --write
```

Use it when:
- a long reasoning or coding run finishes
- you want to see if Rick is overspending on the wrong route
- you want cost per shipped asset or per launch over time
