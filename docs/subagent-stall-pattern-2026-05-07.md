# Subagent Stall Pattern — Investigation Note 2026-05-07

**Observed:** Gateway logs showing repeated "stalled session" warnings for session `agent:main:subagent:24667508-e4f7-4f4e-83f9-8d561c20f6f7`

**Classification:** `stalled_agent_run` — NOT a token-budget issue

## What Happened

The subagent session started ~14.5 hours before detection (estimated ~09:30 AM PT May 6).
By midnight PT it was logged as stalled with `age=52416s+` (14.5h).

Gateway diagnostic fields:
```
state=processing
activeWorkKind=model_call
reason=active_work_without_progress
classification=stalled_agent_run
recovery=none
```

## Root Cause

**A model API call hung indefinitely without timing out.** The subagent was mid-execution and waiting for a model response that never arrived. This is likely caused by:

1. An Anthropic/OpenAI API request that silently dropped (no TCP close, no timeout signal)
2. The subagent runtime has no hard wall-clock timeout on individual model calls
3. The gateway stall detector (`active_work_without_progress`) caught it after ~30s polling, but `recovery=none` means it can't auto-kill and restart

The "subagent announce give up (retry-limit)" errors in the handoff were a downstream symptom: the stalled session never sent its completion event, so the gateway tried to announce completion to the requester, retried 3x, then gave up.

## Is This an OpenClaw Bug?

**Yes, partially.** The gateway correctly detects the stall but has no auto-recovery for `stalled_agent_run`. A model call with no progress for >10 minutes should be killed and the session marked failed/timed-out so the requester gets a clean signal rather than hanging.

## Mitigation (Today)

- This was a prior-session stale session — not blocking current work
- Current session subagents (spawned in this turn) are healthy and completed
- Pattern is reproducible when: long-context session spawns a subagent late in its lifecycle AND that subagent hits a slow/dropped model API call

## Recommendation for OpenClaw Upstream

File issue: "Stalled subagent sessions with `activeWorkKind=model_call` and no progress for >N minutes should be auto-terminated and marked failed, with a structured completion event sent to the requester indicating timeout. Current `recovery=none` leaves requesters hanging indefinitely."

**Workaround:** Prefer shorter, tighter subagent tasks (5-10 min scope). Avoid spawning subagents late in long-context turns where the requester session itself may be compacted.
