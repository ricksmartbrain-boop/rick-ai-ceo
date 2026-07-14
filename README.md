# Rick — Live Runtime Workspace

This directory (`~/.openclaw/workspace`) is **Rick's live runtime**: the working root the
OpenClaw gateway boots from. Rick is an always-on autonomous revenue agent for **MeetRick.ai**
(API live on Railway; product source in `~/meetrick/api`).
Today one main OpenClaw agent (`rick`) stays active; the future `rick-ceo` / `rick-builder` /
`rick-distribution` / `rick-customer-ops` fleet is documented under `docs/` but not yet spawned.

> The gateway process reads files here at bootstrap. Do not restart the gateway or move
> bootstrap docs casually. Treat everything below as operational.

## Canonical files (do not move)

| File | Role |
|------|------|
| `AGENTS.md` | Agent operating contract loaded at bootstrap |
| `CLAUDE.md` | Repo rules (12-rule template) |
| `IDENTITY.md` / `SOUL.md` | Rick's identity and voice — read-only |
| `MEMORY.md` | HOT tacit memory, bootstrap-injected (target <10KB) |
| `MEMORY-WARM.md` / `MEMORY-COLD.md` | Warm/cold memory tiers |
| `HOT-CONTEXT.md` | Symlink → `~/rick-vault/memory/hot-context.md` |
| `HEARTBEAT.md` | Heartbeat checklist |
| `TOOLS.md` | Tool patterns, runtime, routing conventions |
| `runtime-policy.md` | Runtime guardrails |
| `ANTI_PATTERNS.md` | Failure modes to avoid |
| `SELF-FAQ.md` · `PRICING.md` · `USER.md` | Reference cards (pricing is Free / $29 / $499) |
| `INSTALL.md` · `QUICKSTART.md` | Setup runbooks (referenced by `install.sh` / setup scripts) |

## Where things live

- **Code (real, source of truth):** `scripts/`, `runtime/`, `skills/`, `bin/`, `deploy/`,
  `templates/`, `tests/`, `config/` here. `~/clawd/{scripts,runtime,skills,...}` are
  **symlinks** back to these; `~/clawd/config/` is a separate copy — edit both when changing config.
- **State & logs:** `~/rick-vault/` (Obsidian memory, PARA layout, `logs/`, `operations/`).
- **Scheduling:** user crontab (`crontab -l`) + `~/Library/LaunchAgents/ai.rick.*.plist` + `ai.openclaw.*`.
- **Secrets:** `.env` here and `~/clawd/config/rick.env`.

## Archive

Fossil docs and stray scratch are archived (never deleted) under
`~/rick-vault/archives/docs-YYYY-MM-DD/`. Each archive dir has a `moved-manifest.txt`
(`old path -> new path`) so every move is reversible. Latest sweep: `docs-2026-07-13/`.
