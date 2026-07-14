# Heartbeat Targets

Rick uses this file as the human-readable checklist for what must be monitored and how severe each failure is.

## Production Sites

| Name | URL / Command | Expected Result | Severity | Auto-Fix Allowed? | Notes |
|------|----------------|----------------|----------|-------------------|-------|
| `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |

## Runtime Services

| Service | Check | Expected Result | Severity | Restart Policy | Notes |
|---------|-------|----------------|----------|----------------|-------|
| OpenClaw daemon | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |
| Rick daemon | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |
| Telegram bridge | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |

## Revenue Surfaces

| Surface | Check | Expected Result | Severity | Notes |
|---------|-------|----------------|----------|-------|
| Stripe | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |
| Newsletter | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |
| Social distribution | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |

## Long-Running Agents

| Agent / Session | tmux Session Name | Success Signal | Stall Rule | Restart Rule |
|-----------------|-------------------|----------------|------------|--------------|
| `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` | `[TODO]` |

## Approval Escalation

- If severity is `critical`, alert founder immediately via Telegram.
- If severity is `high`, try the safe fix first, then alert with evidence.
- If severity is `medium`, log the issue and include it in the next heartbeat unless revenue is directly impacted.
- Founder-specific overrides: `[TODO]`
