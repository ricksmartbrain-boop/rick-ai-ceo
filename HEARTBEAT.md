# HEARTBEAT.md — Rick Heartbeat Checklist

Use this checklist on every heartbeat. Rick has a runtime loop, but the agent should still reason through the business the way Felix did.

## ⛔ Efficiency Rules (PERMANENT — 2026-04-04)

1. **State-diff only.** If nothing changed since last heartbeat → return `HEARTBEAT_OK`. Do not repeat unchanged status.
2. **Don't re-read static files every cycle.** HEARTBEAT.md, heartbeat-targets.md, launch-playbook.md — only re-read if you have reason to believe they changed.
3. **Cheap model only.** Heartbeat must run on haiku / mini / flash-lite tier. Never pro/opus/thinking.
4. **Don't bloat the daily note.** Heartbeat logs go to `control/briefings/`. Daily note keeps only: plan, blockers, wins, and material state changes.
5. **Scripts first, LLM second.** Let `run-heartbeat.sh` collect facts. Only involve an LLM when state actually changed or anomaly detected.
6. **Fallback stays cheap.** If cheap model unavailable: try another cheap model → then PAUSE + ALERT. Never silently escalate to premium.

## ⛔ Cleanup Guard (PERMANENT — 2026-07-14)

Heartbeat/cleanup sessions (and any non-owner actor) must NEVER cancel workflows or resolve approvals unless the item is **verifiably synthetic**: `@example.com` recipient, `[DRILL]` marker, or `drill-*` tag.

- Real open approvals awaiting the founder are sacred. If cleanup thinks a real item is stale, escalate via Telegram — do not touch it.
- To resolve a synthetic approval, use `python3 runtime/runner.py approve|deny --approval-id <id>` — the runtime enforces this guard deterministically and refuses non-synthetic items for non-owner actors.
- NEVER run raw SQL `UPDATE`/`DELETE` against `approvals`, `workflows`, or `jobs` from a cleanup session, and NEVER pass `--actor telegram` or `--actor vlad` (owner-only actor values) or issue `/cancel` for a non-synthetic workflow.

(Added after the 2026-07-13 incident: a parallel Iris "heartbeat-cleanup" session resolved approval `apr_62dafa5c3a3f` directly.)

## ⛔ Session Weight Rule (PERMANENT — 2026-04-16)

After **25+ exchanges** or **3+ hours** in the current session, flag for rotation consideration:
- Log `SESSION_HEAVY` to heartbeat state with exchange count and session age.
- If the session is purely heartbeat/monitoring (no active user conversation), prefer spawning a fresh session for the next heartbeat cycle.
- If mid-conversation with the founder, finish the current thread, then rotate.
- Never hard-kill a session with pending work — flag and let the next cycle pick up.

Check session weight by reading `heartbeat-state.json` field `session.exchanges` and `session.started_at`. Update these on every heartbeat.

## Load Founder Targets First

Before treating monitoring as complete, read:
- `$RICK_DATA_ROOT/control/heartbeat-targets.md`
- `$RICK_DATA_ROOT/control/launch-playbook.md`

If those files still contain `[TODO]` markers, monitoring and launch confidence are partial by definition.

---

## Heartbeat Batching System

Not every check needs to run every beat. Use the tiered system below to avoid redundant work and keep heartbeats fast.

**Before running any check**, read `heartbeat-state.json` (default: `$RICK_DATA_ROOT/control/heartbeat-state.json`). Compare `last_check` timestamps against `min_interval_minutes` for each check. Skip any check whose interval hasn't elapsed.

**After each heartbeat**, write updated timestamps back to `heartbeat-state.json`.

### Tier 1 — Always (every heartbeat)

These run on every single beat. They are the core operational pulse.

| Check | Min Interval | Rationale |
|-------|-------------|-----------|
| Execution Check | 0 min (always) | Plan vs. actual is the whole point |
| Site Health Check | 15 min | Don't hammer health endpoints faster than this |
| Long-Running Agent Health (watchdog) | 15 min | Process state doesn't change faster than this |
| Fast Runtime Loop | 0 min (always) | Surfaces queued work and approvals |

Even within Tier 1, respect `min_interval_minutes` from state. If site health was checked 5 minutes ago, skip it this beat.

### Tier 2 — Rotate (2–4× per day)

These are important but not beat-critical. Spread them across the day. On each heartbeat, pick **at most one** Tier 2 check to run (round-robin or least-recently-checked).

| Check | Min Interval | Rationale |
|-------|-------------|-----------|
| Moltbook engagement | 4 hours | Social presence, not a firehose |
| Memory refresh / index rebuild | 6 hours | Vault doesn't change that fast |
| Fact extraction | 4 hours | Extract after meaningful work accumulates |

Selection logic:
1. Read `last_check` for all Tier 2 items.
2. Filter to those past their `min_interval_minutes`.
3. Pick the one with the oldest `last_check`.
4. Run it. Update state. Move on.

If no Tier 2 item is due, skip the tier entirely.

### Tier 3 — Daily Only

These have their own scripts and run once per cycle, not per heartbeat.

| Check | Schedule | Script |
|-------|----------|--------|
| Nightly deep dive | Once/day, late evening | `bash scripts/run-nightly.sh` |
| Weekly synthesis | Once/week, Sunday or Monday | `bash scripts/run-weekly.sh` |

The heartbeat should **not** trigger these. They are invoked by cron, the daemon loop, or manual command. The heartbeat only checks whether they ran recently enough (via `heartbeat-state.json`) and flags if overdue.

- Nightly overdue: `last_check` > 36 hours ago → log `NIGHTLY_OVERDUE` warning.
- Weekly overdue: `last_check` > 9 days ago → log `WEEKLY_OVERDUE` warning.

---

## Execution Check (every heartbeat)

1. Read today's plan from `$RICK_DATA_ROOT/memory/YYYY-MM-DD.md` under `## Today's Plan`.
2. Check progress against each planned item.
3. Inspect runtime state: queued jobs, blocked jobs, open approvals, active workflows.
4. If something is blocked, unblock it or escalate through the control plane.
5. If ahead of plan, pull the next priority forward.
6. If there is no urgent revenue fire, default to the MRR grinder loop in `$RICK_DATA_ROOT/control/mrr-grinder-loop.md`.
7. If no traffic, outreach, acquisition, or client-facing action has shipped in the last 6 hours, the heartbeat should usually execute one before ending. Monitoring alone does not satisfy the beat in that case.
8. One-time (Sat 2026-07-25, after 09:30 PT): verify the weekly newsletter run — `~/clawd/skills/newsletter/issues/issue-0NN.json` written, ONE-ISSUE INSERT deleted from `~/rick-vault/projects/newsletter/weekly-newsletter-prompt.md`, `sent_at` set in `~/rick-vault/control/lingualive-arm.json`, cron job `a98e7d9e` `last_run_status='ok'` in `~/.openclaw/state/openclaw.sqlite` — if the run FAILED, page Vlad via Telegram immediately (the cron's own failure alert does not deliver: `state_json` `lastFailureNotificationDelivered=false` from the 2026-07-18 attempt). Remove this item after Jul-25.
9. Log progress to the daily note.

## Site Health Check (every heartbeat)

1. Check production sites return expected health.
2. Run `bash scripts/health-check.sh` against `RICK_HEALTH_TARGETS_FILE` for configured URL/process targets.
3. If a site is down, alert immediately.
4. If the issue is reversible and diagnosable, fix first, then alert with the real cause.

## Long-Running Agent Health Check (every heartbeat)

1. Read the daily note section `## Active Long-Running Processes`.
2. Read `config/watchdog-processes.json` as the managed restart registry.
3. Run the watchdog with `bash skills/self-healing-ops/scripts/watchdog.sh` and review `$RICK_DATA_ROOT/control/watchdog-report.md`.
4. If alive, inspect recent output for progress.
5. If dead or missing and the restart path is safe, auto-restart it.
6. If stalled for 2+ heartbeats, kill and restart with fresh context.
7. If finished successfully, log completion and remove it from the active list.

## Fact Extraction (Tier 2 — rotated)

1. Extract durable facts from today's work into the vault.
2. Update project summaries and items where needed.
3. Preserve what should survive context compaction.
4. Refresh the vault memory index so hot/warm/cold recall stays current.

## Fast Runtime Loop

```bash
python3 runtime/runner.py heartbeat --work-limit 2
```

Use it to:
- count active workflows
- count queued and blocked jobs
- surface approvals
- move a small amount of work forward

## Operating Heartbeat

```bash
bash scripts/run-heartbeat.sh
```

Use it to:
- bootstrap state
- run Felix-style health-target checks plus Rick service checks
- apply watchdog restart policy
- refresh memory index
- refresh executive context
- refresh the scoreboard

Heartbeat is not a vague reflection loop. It exists to do what Felix proved matters in production:
- compare plan vs actual execution
- verify service and process health
- extract durable facts into memory
- keep inbox and operational queues from stalling
- push traffic, outreach, acquisition, and client movement when no more urgent fire exists

## Nightly Deep Dive

```bash
bash scripts/run-nightly.sh
```

Nightly rules:
1. Always review the previous complete calendar day for revenue, not the current partial day.
2. Review what got done from today's plan.
3. Review what failed or stalled and why.
4. Propose tomorrow's 3-5 highest-impact actions.
5. Refresh the briefings and reflection artifacts.

## Weekly Synthesis

```bash
bash scripts/run-weekly.sh
```

Weekly rules:
- rewrite active summaries from facts
- cool stale context without deleting it
- rebuild the memory overview from the latest vault state
- review portfolio ranking
- review experiments, blockers, and launch quality

## Always-On Loop

```bash
bash scripts/run-daemon.sh
```

Purpose:
- keep Rick alive on the dedicated machine
- continue queued workflows
- refresh dashboards continuously
- surface runtime failures quickly

## Moltbook (Tier 2 — rotated)
1. Check feed: `curl -s "https://www.moltbook.com/api/v1/home" -H "Authorization: Bearer $MOLTBOOK_API_KEY"`
2. Engage with relevant posts from other agents
3. Post when there's something worth sharing (launches, lessons, real numbers)
4. API key in: `~/.config/moltbook/credentials.json` and `MOLTBOOK_API_KEY` env var

---

## Heartbeat State File

Location: `$RICK_DATA_ROOT/control/heartbeat-state.json`

The heartbeat reads and writes this file every cycle. It is the gate that prevents redundant checks.

**Read it** at the start of every heartbeat. **Write it** at the end with updated timestamps.

Schema: see `heartbeat-state-schema.json` for the full structure.

Key rules:
- If the file is missing or corrupt, treat all checks as due (cold start) and recreate it.
- Timestamps are ISO 8601 UTC.
- `run-heartbeat.sh` should source this file for skip/run decisions where possible (bash can read JSON with `jq`).
- Never delete the file. Only overwrite with valid JSON.
