---
name: metrics
description: Pull revenue and business metrics across Stripe accounts. Use when checking daily/weekly/monthly revenue, running nightly deep dives, comparing periods, or answering any question about sales performance.
---

# Metrics

Pull consolidated revenue metrics across your Stripe accounts.

## Setup

1. Store your Stripe secret key:
```bash
mkdir -p ~/.config/stripe
echo "sk_live_..." > ~/.config/stripe/api_key
chmod 600 ~/.config/stripe/api_key
```

2. Copy `config/stripe-accounts.example.json` to your real Stripe accounts file and point `RICK_STRIPE_ACCOUNTS_FILE` at it.

3. Run:
```bash
python3 {baseDir}/scripts/stripe-metrics.py --period today
python3 {baseDir}/scripts/stripe-metrics.py --period week
python3 {baseDir}/scripts/stripe-metrics.py --period month
```

Output is JSON with per-account and aggregate numbers: gross revenue, refunds, net revenue, transaction count, and period-over-period growth %.

## Nightly Deep Dive Workflow
1. Run `--period today` for the daily snapshot
2. Run `--period month` for trend context
3. Write findings to `memory/YYYY-MM-DD.md` under "## Revenue Review"
4. Propose next day's plan based on what's working

## Key Metrics to Track
- **Daily net revenue** — the scoreboard
- **Per-account breakdown** — which products are pulling weight
- **Period growth %** — are we accelerating or decelerating
- **Transaction count** — volume vs ticket size trends
