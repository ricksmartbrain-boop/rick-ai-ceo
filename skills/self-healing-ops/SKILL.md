---
name: self-healing-ops
description: Watch Rick's critical services, sessions, and local operating surfaces. Use when running heartbeats, detecting outages, checking tmux sessions, or recovering from infrastructure drift.
---

# Self-Healing Ops

This skill gives Rick a real repair loop instead of a purely descriptive heartbeat.

## Scripts

### Service Check

```bash
scripts/service-check.sh
```

Reads `RICK_SITES_FILE` and writes `$RICK_DATA_ROOT/control/ops-health.md`.

### Session Recover

```bash
scripts/session-recover.sh --name pc-fix --cmd "cd /repo && ralphy --codex --prd PRD.md"
```

### Watchdog

```bash
scripts/watchdog.sh
```

Managed by:
- `config/watchdog-processes.json`
- `$RICK_DATA_ROOT/control/watchdog-report.md`
- `$RICK_DATA_ROOT/control/recovery-log.md`
- `$RICK_DATA_ROOT/operations/watchdog-state.json`

## Rules

- detect first
- fix if the recovery path is safe and known
- if a missing credential or approval blocks the fix, surface it immediately
- do not restart endlessly; use cooldowns and daily restart caps
