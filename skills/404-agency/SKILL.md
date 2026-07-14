# 404 Agency Skill

Manage 404 Model Agency operations -- content scheduling, metrics tracking, and revenue monitoring.

## Models
| Model | Real Name | Instagram | Fanvue |
|-------|-----------|-----------|--------|
| Cat | Catalina Montes | @_catalina_montes | catalina_montes |
| Luna | Luna Solano | @_luna_solano_ | -- |

## Commands

### agency-metrics.sh
Pull social media metrics via SociaVault API.
```bash
bash scripts/agency-metrics.sh              # Both models
bash scripts/agency-metrics.sh --model cat   # Cat only
bash scripts/agency-metrics.sh --model luna  # Luna only
```

### content-schedule.sh
Review content pipeline and suggest posting schedule.
```bash
bash scripts/content-schedule.sh             # Show pending content
bash scripts/content-schedule.sh --week      # Weekly content plan
```

### agency-status.sh
Full agency dashboard.
```bash
bash scripts/agency-status.sh                # Complete status
```

## Infrastructure
- **Bot:** ~/telegram-sync/ (Python, launchd)
- **Group:** Telegram -1003289142566
- **Scraping:** SociaVault API (every 6h via bot)
- **Classification:** Claude Haiku (every 30min via bot)
- **Vault:** ~/telegram-sync/vault-output/404 Model Agency/

## Bot Commands (via Telegram)
- `/classify` -- Trigger Claude batch classification
- `/weekly` -- Generate weekly summary
- `/status` -- Show bot stats
- `/metrics cat instagram 150` -- Manually log metrics

## Revenue Target: $5,000/month
- Fanvue subscriptions
- Brand partnerships
- Content licensing (future)
