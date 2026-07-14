# Runtime Policy

## 1. Lane Separation
Use four hard-routed lanes.

### Lane A, Watch / Health
Scope: uptime checks, heartbeat, log drift, session checks, alerting.
Model: cheap/default only.
Escalate only on repeated failure or critical outage.

### Lane B, Triage / Plan
Scope: classify, summarize, rank, decide next action, draft messages.
Model: primary reasoning model, with cheap fallback for low-risk summaries.
No direct writes outside approved targets.

### Lane C, Build / Act
Scope: code edits, config changes, command execution, file writes.
Model: strongest available for multi-step work.
Requires explicit objective, bounded workspace, and rollback path.

### Lane D, External / High-risk
Scope: outbound messages, posts, deletions, payments, auth, prod changes.
Model: strongest available, with a policy gate and confirmation.
Default deny unless explicitly requested.

## 2. Fail-Closed Cheap Paths
If routing is uncertain, cost is high, context is thin, or a tool/model errors twice, fail closed.

Fallback order:
1. Summarize only
2. Ask for clarification
3. Defer
4. Escalate to human

Rules:
- Never auto-promote from cheap to expensive without a new trigger.
- Ambiguous external actions become no-op plus clarification.
- Missing target, missing diff, or missing blast radius blocks writes.

## 3. Budget Caps
Per-lane caps are enforced daily and per task.

Suggested caps:
- Lane A: $0.05/task, $1/day
- Lane B: $0.50/task, $5/day
- Lane C: $2/task, $20/day
- Lane D: $1 reasoning plus explicit confirmation per action, $10/day hard stop

Global caps:
- Soft warning at 70% of daily budget
- Hard stop at 90%
- Absolute stop when any lane exceeds cap, route to summarize-only until reset

Cost controls:
- Prefer cached context, short prompts, and smallest adequate model.
- Retry only once on transient failure.
- No speculative second model unless the first returns malformed output.

## 4. Default Model Assignments
- Watch / Health: Haiku or equivalent cheapest reliable model
- Triage / Summary: Haiku
- Planning / Decision: Opus or Sonnet
- Coding / Editing: Codex first, then Opus only if needed
- High-risk approvals: Opus or Sonnet, never autonomous

Practical rule: use the cheapest model likely to succeed, then escalate once if needed.

## 5. Watchdog and Restart Policy
Watchdog checks:
- process alive
- heartbeat fresh
- session not hung
- output advancing
- no crash loop

Restart policy:
- First failure: restart session or process
- Second failure in 15 minutes: restart with reduced mode, cheaper model, smaller context
- Third failure in 1 hour: stop automation and escalate to human review

Hung session thresholds:
- Lane A: 5 min
- Lane B: 10 min
- Lane C: 15 min
- Lane D: 5 min

Crash-loop guard:
- Max 3 restarts/hour per service
- If exceeded, disable auto-restart and alert

## 6. Escalation Rules
Escalate when:
- repeated model or tool failure
- ambiguous destructive action
- auth, pairing, or security issue
- budget cap hit
- permission denied from an external service
- task crosses from draft to irreversible act
- watchdog detects drift after restart
- confidence is low and consequences are high

Escalation format:
- what happened
- why automation stopped
- safest next action
- exact command or decision needed

## 7. Implementation Rules
- Every task gets a lane label at intake.
- Every lane has allowed tools, allowed models, and budget.
- Every write action must produce a diff or explicit target.
- Every external action needs a confirmation gate.
- Every long-running session must emit heartbeat markers.
- Every failure path must be deterministic and cheap.

## 8. Routing Table
- Ping / health / session check / log scrape / brief summary -> Lane A, cheap model
- Rank priorities / summarize day / draft response -> Lane B, opus
- Edit code / refactor / multi-file reasoning -> Lane C, opus or strong code model
- Post / send / delete / secrets / deploys / payments -> Lane D, opus plus human gate
