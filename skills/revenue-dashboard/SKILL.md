# Revenue Dashboard Skill

Unified cross-product revenue intelligence. Aggregates Stripe data, tracks MRR by product, compares against $100K/month target.

## Triggers
- **Nightly review** (2-3 AM): Automatic revenue snapshot
- **Manual:** `/revenue` command
- **Weekly synthesis** (Sunday 3 AM): Weekly P&L by product

## Commands

### revenue-report.py
Cross-product revenue report from Stripe.

```bash
# Yesterday's revenue
python3 scripts/revenue-report.py --period yesterday

# Month-to-date
python3 scripts/revenue-report.py --period month

# Week-to-date
python3 scripts/revenue-report.py --period week

# Full report (all periods)
python3 scripts/revenue-report.py --full
```

### revenue-cumulative.py
Aggregate daily snapshots into a running cumulative tracker.

```bash
python3 scripts/revenue-cumulative.py
```

## Output
- Console: Formatted markdown report
- File: ~/rick-vault/revenue/YYYY-MM-DD.md (daily snapshot)
- File: ~/rick-vault/revenue/cumulative.json (running total)
- File: ~/rick-vault/revenue/SUMMARY.md (proof loop status)

## Revenue Targets
| Product | Target MRR |
|---------|------------|
| Partner Connector | $30,000 |
| 404 Model Agency | $5,000 |
| Personal Brand | $15,000 |
| Info Products | $40,000 |
| LinguaLive | $10,000 |
| **Total** | **$100,000** |

## Metrics Tracked
- MRR (monthly recurring revenue) per product
- New customer count and revenue
- Churned customer count and lost revenue
- Net revenue (new - churned)
- Growth rate (week-over-week, month-over-month)
- Gap to target per product
- Estimated months to target at current growth rate

## Integration
- Reads: Stripe API (via stripe-metrics.py or direct)
- Writes: ~/rick-vault/revenue/YYYY-MM-DD.md
- References: ~/rick-vault/okrs/ for target comparison
