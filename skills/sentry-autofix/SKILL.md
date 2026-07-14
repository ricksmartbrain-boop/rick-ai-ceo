# Sentry Auto-Fix Skill

Monitor Sentry issues, triage regressions, and prepare fixes or coding-agent loops for real production errors.

## Purpose

Full Sentry → Triage → Codex/Ralph → PR → Notification pipeline.

Use it when:
- a production site is failing
- Sentry has new high-severity issues
- repeated regressions need structured triage
- a coding agent should be launched against a concrete bug report

## Inputs

- `SENTRY_AUTH_TOKEN`
- `SENTRY_ORG`
- `SENTRY_PROJECT`
- `SENTRY_GITHUB_REPO` (e.g. `owner/repo`) — for PR creation
- `SENTRY_WEBHOOK_SECRET` (optional) — for webhook signature verification

## Pipeline Flow

```
Sentry Issue → Webhook POST → sentry-webhook-server.py
  → sentry-autofix.py webhook (triage + score)
    → score >= 40: autofix (Codex → Ralph fallback → PR)
    → score >= 20: review-flagged (Telegram notification)
    → score < 20: monitor (logged, no action)
```

## Commands

### List issues
```bash
scripts/sentry-issues.sh --list
scripts/sentry-issues.sh --list --severity error
scripts/sentry-issues.sh --issue ISSUE_ID
```

### Triage a specific issue
```bash
python3 scripts/sentry-autofix.py triage --issue-id ISSUE_ID
```

### Generate fix spec (dry run)
```bash
python3 scripts/sentry-autofix.py fix --issue-id ISSUE_ID --dry-run
```

### Dispatch autofix
```bash
python3 scripts/sentry-autofix.py fix --issue-id ISSUE_ID
```

### Run webhook server
```bash
python3 scripts/sentry-webhook-server.py --port 9876
# Configure Sentry to POST to http://yourhost:9876/sentry
```

### Process webhook from stdin
```bash
cat webhook-payload.json | python3 scripts/sentry-autofix.py webhook
```

## Triage Scoring

| Factor | Points |
|--------|--------|
| fatal severity | +40 |
| error severity | +25 |
| warning severity | +10 |
| count > 100 | +20 |
| count > 10 | +10 |
| users > 10 | +15 |
| is regression | +20 |
| revenue-affecting keywords | +25 |

- **autofix** (score >= 40): dispatch Codex, fallback to Ralph, create PR
- **review** (score >= 20): notify Telegram, generate fix spec for manual review
- **monitor** (score < 20): log only

## Operating Rules

- prioritize fresh regressions over noisy long-tail issues
- look for revenue-affecting surfaces first
- Codex is primary fix agent; Ralph is fallback for longer loops
- do not mark an issue fixed without verification in the target environment
- all triage decisions logged to `~/rick-vault/operations/sentry-autofix/`
