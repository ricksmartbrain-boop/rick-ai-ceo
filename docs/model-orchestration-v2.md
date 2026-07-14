# Model Orchestration & Token Optimization — v2

Last updated: 2026-03-13
Owner: Rick
Status: Proposed canonical policy

## Goal

Maximize business output per dollar by routing each task to the cheapest model that is good enough, escalating only when stakes or uncertainty justify it.

This policy replaces ad hoc model selection with a measurable economic system.

---

## Core Principles

1. **Cheap first, smart second, premium last**
   - Default to the lowest-cost model that reliably completes the task.
   - Escalate only for ambiguity, high stakes, or repeated failure.

2. **Route by business intent, not by provider preference**
   - The route is the abstraction layer.
   - Models can be swapped without changing prompts or workflows.

3. **Optimize for shipped outcomes, not benchmark vanity**
   - The best model is the one that produces the highest-quality business result per dollar and per minute.

4. **Reserve premium reasoning for asymmetric decisions**
   - Strategy, irreversible choices, high-value synthesis, and final review.

5. **Every call should be traceable**
   - Log route, model, tokens, cost, latency, and outcome.

---

## Canonical Route Map

### 1) Heartbeat
Purpose: recurring operational checks, triage, low-stakes parsing

- Primary: `claude-3-5-haiku`
- Fallback 1: `gpt-5.3-codex`
- Fallback 2: `claude-sonnet-4-6`

Use for:
- heartbeat summaries
- inbox triage
- routine classification
- status parsing
- queue inspection narration

Do not use for:
- irreversible recommendations
- strategy synthesis
- customer escalations with financial/legal risk

### 2) Ops
Purpose: founder chat, Telegram replies, customer operations, normal assistant work

- Primary: `claude-sonnet-4-6`
- Fallback 1: `gpt-5.4`
- Fallback 2: `gpt-5.3-codex`

Use for:
- normal Telegram interaction
- support replies
- internal summaries
- workflow progress updates
- execution planning under normal ambiguity

### 3) Writing
Purpose: copy, launch assets, newsletters, posts, sales messaging

- Primary: `claude-sonnet-4-6`
- Fallback 1: `gpt-5.4`
- Fallback 2: `claude-opus-4-6`

Use for:
- landing page copy
- social posts
- launch announcements
- email/newsletter drafts
- polished customer-facing writing

Escalate to Opus only when:
- brand voice is highly important
- positioning is still unclear
- the copy is strategically important and public

### 4) Coding
Purpose: implementation, debugging, repo work, tests

- Primary: `gpt-5.3-codex`
- Fallback 1: `claude-sonnet-4-6`
- Fallback 2: `gpt-5.4-pro`

Use for:
- writing code
- fixing bugs
- editing scripts
- tests and verification support

Escalate to stronger model when:
- system design spans many files/components
- repeated failed fixes occur
- architecture tradeoffs matter more than raw edit throughput

### 5) Strategy
Purpose: high-stakes planning, prioritization, synthesis, founder decisions

- Primary: `claude-opus-4-6`
- Fallback 1: `gpt-5.4-pro`
- Fallback 2: `gpt-5.4`

Use for:
- offer strategy
- roadmap prioritization
- growth bets
- major tradeoff analysis
- postmortems / retrospectives

Do not use by default for:
- routine chat
- simple drafting
- low-value internal summaries

### 6) Research
Purpose: current-state analysis, market scanning, research-backed synthesis

- Primary: `gpt-5.4`
- Fallback 1: `claude-sonnet-4-6`
- Future target: Grok route when fully wired and verified

Use for:
- market research
- competitive analysis
- current web-backed investigation
- synthesis of recent signals

### 7) Review
Purpose: final QA for high-stakes output

- Primary: `claude-opus-4-6`
- Fallback 1: `gpt-5.4-pro`

Use for:
- final launch review
- financial/compliance-sensitive wording check
- strategic memo review
- final customer escalation review

---

## Global Fallback Policy

If a primary model fails due to timeout, rate limit, provider issue, or poor output quality:

1. Retry once on the same model only if failure is clearly transient.
2. Otherwise step to the route fallback.
3. If the route fallback also fails, step to global emergency fallback:
   - `claude-sonnet-4-6`
   - `gpt-5.4`
   - `gpt-5.3-codex`
4. Log all fallback events.

---

## Escalation Rules

Start cheap. Escalate when any of these are true:

### Escalate one tier if:
- prompt spans multiple goals
- ambiguity remains after first pass
- output needs stronger structure or judgment
- first draft is directionally right but weak
- workflow has already consumed one failed attempt

### Escalate two tiers if:
- decision affects revenue materially
- output is customer/public facing and high-visibility
- recommendation is hard to reverse
- failure could cause legal, trust, or payment issues
- task requires portfolio-level prioritization or deep synthesis

### Never escalate just because:
- the task feels “important” emotionally
- the user used words like “best” or “smartest” without actual stakes
- a cheap model produced acceptable output already

---

## Hard Budget Policy

Budgets should be enforced daily and reviewed weekly.

### Daily route budgets
- Heartbeat: low fixed cap
- Ops: moderate cap
- Writing: moderate cap with temporary launch spikes allowed
- Coding: moderate/high cap when tied to shipping
- Strategy: small cap, premium-only by exception
- Research: moderate cap, tied to concrete decision or asset
- Review: very small cap, only final-pass high-stakes use

### Economic rules
- Premium models must not dominate total token spend.
- Strategy + Review together should stay a minority of total daily calls.
- Heartbeat should be the cheapest route by far on a per-call basis.
- If a route consistently needs escalation, improve prompts or reassign the primary model.

---

## Logging Spec

Every model call should log:

- timestamp
- workflow/session id
- route
- task label
- model
- provider
- input tokens
- output tokens
- estimated cost
- latency ms
- success/failure
- escalation_from (optional)
- fallback_used (bool)
- artifact/result id (optional)
- business outcome tag

### Outcome tags
Use one or more:
- `chat`
- `ops`
- `draft`
- `publish`
- `ship`
- `support`
- `approval`
- `research`
- `strategy`
- `review`
- `revenue`

---

## Weekly Review Metrics

Every week, review:

1. Spend by model
2. Spend by route
3. Average cost per completed task
4. Average cost per shipped artifact
5. Average cost per public post / launch asset
6. Premium escalation rate
7. Fallback frequency
8. Tasks with repeated model failure
9. Cost linked to revenue events where traceable

Questions to ask:
- Which route is overspending?
- Which tasks are being over-modeled?
- Which prompts are causing unnecessary escalation?
- Where are premium models genuinely paying for themselves?

---

## Architecture Pattern

```text
User / Workflow / Heartbeat
        ↓
Intent classifier (heartbeat | ops | writing | coding | strategy | research | review)
        ↓
Route policy lookup
        ↓
Primary model call
        ↓
Quality / failure check
        ├─ acceptable → return result + log
        └─ weak/fail → escalate or fallback + log
        ↓
Persist telemetry
        ↓
Weekly budget + performance review
```

---

## Immediate Implementation Priorities

### Phase 1 — Canonicalize
1. Put this route map into one real config file.
2. Make all internal workflows use route names, not raw model IDs.
3. Mark Gemini as disabled for all routes.

### Phase 2 — Instrument
1. Log every model call with route + cost metadata.
2. Produce a simple daily usage summary.
3. Produce a weekly model economics report.

### Phase 3 — Govern
1. Add route budgets.
2. Add escalation thresholds.
3. Alert when premium usage exceeds expected share.

### Phase 4 — Optimize
1. Move repeated low-stakes tasks to cheaper models.
2. Improve prompts for routes with high escalation rates.
3. Use stronger models only where outcome delta is proven.

---

## Non-Negotiables

- No Gemini routing until explicitly re-approved.
- No premium strategy model for routine chat.
- No “smartest model by default” policy.
- No cost optimization without quality tracking.
- No quality optimization without cost tracking.

---

## Recommended Default Operating Posture

- **Default workhorse:** `claude-sonnet-4-6`
- **Cheap utility model:** `claude-3-5-haiku`
- **Coding specialist:** `gpt-5.3-codex`
- **Premium strategic model:** `claude-opus-4-6`
- **Premium fallback / alternative reasoning:** `gpt-5.4-pro`
- **General research model:** `gpt-5.4`

This is the best current balance of quality, speed, and token economics for Rick.
