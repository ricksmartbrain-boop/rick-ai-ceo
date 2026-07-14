# Provider Safety — Claude, Gemini, and OpenClaw

This file records the compliant low-risk path for using Claude and Gemini through Rick on OpenClaw.

## Core Rule

OpenClaw itself is not what gets accounts banned.

Risk comes from:
- using consumer products instead of official API or official CLI access
- violating provider usage policies
- operating from unsupported regions
- ignoring rate limits and quotas
- spammy or deceptive automation
- trying to bypass bans, guardrails, or provider restrictions

## Safe Path For Claude

Use one of these:
- Anthropic API key under Anthropic commercial terms
- Claude Code authenticated through the official CLI flow
- Claude on Vertex AI if you prefer Google Cloud controls

Do not:
- automate the Claude web app like a browser bot
- share one Claude account across unauthorized users
- use Claude from unsupported regions
- use it for spam, ban evasion, guardrail bypassing, or model scraping

## Safe Path For Gemini

Use one of these:
- Gemini API key in Google AI Studio
- Gemini through Vertex AI

Do not:
- automate consumer Gemini web surfaces instead of the API
- exceed or try to circumvent quotas or rate limits
- use it from unsupported regions
- shard usage across projects or accounts to avoid quotas
- use it for spam, abuse, or deceptive automation

## Safe Path For OpenClaw

Use OpenClaw as:
- the runtime
- the scheduler
- the channel/router
- the local control plane

Do not use OpenClaw to:
- impersonate human browser sessions on consumer apps
- mask identity of the API client
- bypass provider restrictions
- rotate through multiple accounts to avoid provider controls

OpenClaw is safest when it calls official APIs and official CLIs only.

## Operational Rules Rick Should Follow

- API-first, not browser-first
- one account per provider with legitimate business access
- one clear owner for each API key
- keep usage inside supported regions
- respect provider rate limits and back off on 429 / quota errors
- prefer official CLI auth flows over copied session cookies
- do not use outputs to train competing models without provider authorization
- do not send spammy bulk content or deceptive outreach
- keep founder review for risky actions, especially finance, access, billing, and mass outbound

## Recommended Mac Studio Setup

- Anthropic:
  - `ANTHROPIC_API_KEY` in `config/rick.env`, or official Claude Code auth
- Google:
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY` in `config/rick.env`
- OpenClaw:
  - use `openclaw onboard`
  - use model allowlists and explicit aliases
- Optional:
  - use LiteLLM with virtual keys and spend limits so Rick does not create suspicious burst patterns

## Highest-Risk Behaviors To Avoid

- web-app automation of `claude.ai` or consumer Gemini surfaces
- spammy automated outreach at scale
- multi-account ban evasion
- guardrail bypassing / jailbreak-style use
- unsupported-region access
- quota sharding
- undocumented or disguised client identity patterns

## Practical Conclusion

If Rick uses:
- official Anthropic API / Claude Code auth
- official Gemini API / Vertex
- OpenClaw as the orchestrator
- reasonable budgets, backoff, and business-purpose workflows

then the setup is on the normal compliant path.
