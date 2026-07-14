# Claw Mart Skill

Package and list Rick's skills on the OpenClaw Claw Mart marketplace for distribution and revenue.

## Purpose

Claw Mart is OpenClaw's skills marketplace (10% fee + $20/mo creator sub). This skill manages:
- Skill packaging and export
- Manifest generation for marketplace listing
- Version management

## Listing Strategy

### Tier 1 — Free (lead magnets)
- **obsidian-memory** — PARA vault setup + memory index for any agent
- **execution-ledger** — action tracking + audit trail

### Tier 2 — $29/skill
- **sentry-autofix** — Sentry → triage → Codex/Ralph → PR pipeline
- **revenue-dashboard** — multi-account Stripe revenue tracking + scoreboard
- **email-automation** — drip sequences, triage, dispatch

### Tier 3 — $99/bundle
- **Rick CEO Starter Kit** — executive-orchestrator + revenue-dashboard + info-products + execution-ledger
- **Agent Ops Pack** — self-healing-ops + sentry-autofix + coding-agent-loops + agent-ops-playbook

## Commands

### package-skill.sh
Export a skill directory into Claw Mart distribution format.
```bash
bash scripts/package-skill.sh --skill sentry-autofix --version 1.0.0
bash scripts/package-skill.sh --skill revenue-dashboard --version 1.0.0
```

### generate-manifest.sh
Generate the marketplace manifest for a skill.
```bash
bash scripts/generate-manifest.sh --skill sentry-autofix --price 29 --tier paid
bash scripts/generate-manifest.sh --skill obsidian-memory --price 0 --tier free
```

### list-exportable.sh
Show which skills are ready for marketplace export.
```bash
bash scripts/list-exportable.sh
```

## Manifest Format

```json
{
  "name": "sentry-autofix",
  "version": "1.0.0",
  "description": "Sentry → triage → Codex → PR autofix pipeline",
  "author": "rick",
  "price_usd": 29,
  "tier": "paid",
  "tags": ["sentry", "autofix", "devops", "pr-automation"],
  "files": ["SKILL.md", "scripts/"],
  "requires": ["SENTRY_AUTH_TOKEN", "gh"]
}
```

## Revenue Projection

| Listing | Price | Target Sales/mo | MRR |
|---------|-------|-----------------|-----|
| Free skills (2) | $0 | — | $0 (lead gen) |
| Paid skills (3) | $29 | 30 each | $2,610 |
| Bundles (2) | $99 | 15 each | $2,970 |
| **Total** | | | **$5,580** |
