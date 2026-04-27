# Rick Install

## Today

Use `scripts/install-rick.sh` for a new tenant on a clean Mac.

What it does:
1. validates macOS, Python 3.12+, ffmpeg, Chrome, and git
2. clones `https://github.com/ricksmartbrain-boop/rick-ai-ceo.git` to `~/clawd-rick-<timestamp>`
3. creates `~/.rick-<tenant_id>/` with `rick.env.template` and tenant `rick.env`
4. initializes `~/rick-vault-<tenant_id>/db/rick.db` via `runtime/db.py` migrations
5. installs tenant-scoped LaunchAgents
6. smoke-tests heartbeat + cold-email path

Example:

```bash
bash scripts/install-rick.sh --tenant-id acme-001 --test-email test@example.com
```

For a scaffold-only dry run:

```bash
bash scripts/install-rick.sh --tenant-id acme-001 --dry-run
```

## Slash command

`/install` is registered in `runtime/engine.py` alongside the other Telegram/TUI commands and wraps `scripts/install-rick.sh`.

## P1 multi-tenant hard parts: flag only

These are explicitly not shipped in this first step.

- per-tenant DB isolation across every runtime path
- per-tenant Stripe customer ID mapping
- per-tenant cost limits / budget enforcement
- billing webhook → tenant activation

Notes:
- do not add Stripe/payment paths here
- keep single-tenant Vlad deployment untouched
- multi-tenant activation should be a separate P1 implementation plan, not mixed into the installer
