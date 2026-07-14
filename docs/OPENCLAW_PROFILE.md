# OPENCLAW_PROFILE.md — Rick OpenClaw Profile Layer

This file explains the OpenClaw-native pre-setup layer Rick expects on the Mac Studio.

## Active Now

Only one OpenClaw agent should be active now:

- `rick`

Rick remains the owner of:

- founder-control Telegram topics
- workflow Telegram topics
- approvals
- launch readiness
- publish orchestration

This keeps the first deployment simple and reliable.

## Prepared Now, Activated Later

These future agents are documented in `config/openclaw-agent-blueprint.json` but should stay inactive at first:

- `rick-ceo`
- `rick-builder`
- `rick-distribution`
- `rick-customer-ops`

The blueprint defines purpose, tools, sandbox posture, allowed channels, and workflow ownership so the future split is explicit rather than improvised.

## Session Policy

Use `config/openclaw-session-policy.json` as the Rick-side reference for OpenClaw session behavior.

It is designed to prepare for:

- `session.maintenance` in enforce mode
- topic-session retention and disk limits
- secure customer/support DM scope kept in `prepared` mode
- a memory-flush prompt that writes important state into the Rick Vault before compaction

This repo does not install those settings into OpenClaw automatically. It prepares the files and validates them before Mac setup.

## Session Keys

When a workflow is bound to a Telegram topic, Rick should persist both:

- `telegram_target`
- `openclaw_session_key`

For workflow topics the session key format is:

`agent:rick:telegram:group:<chat_id>:topic:<thread_id>`

This gives Rick a stable OpenClaw-native reference for session-aware handoffs later.

## Secure DMs

Customer/support Telegram DMs are intentionally **prepared but off**.

That means:

- founder-control Telegram remains the only active Telegram surface
- customer/support DM handling must not be enabled until launch stability is proven
- when enabled later, DM scope should isolate peer context and never mix customer conversations with founder-control topics

## Future Specialist Handoffs

When you later split Rick into more OpenClaw agents, the main Rick agent should remain the workflow owner.

Specialist agents should be invoked through OpenClaw session tools:

- `sessions_spawn` for isolated deep work
- `sessions_send` for targeted follow-up in an existing workflow session

The workflow topic remains the shared boardroom. Rick is the conductor; specialists are scoped contributors.
