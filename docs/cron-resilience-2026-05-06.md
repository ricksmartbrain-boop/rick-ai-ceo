# Cron Resilience — Post-Mortem & Plan

**Date:** 2026-05-06
**Author:** Rick (Plan-agent synthesis, Claude Opus 4.7)
**Trigger:** 01:53 PT gateway restart cascade

## TL;DR

At 01:53:20 PT today, `openclaw doctor --fix` reinstalled the gateway LaunchAgent and the daemon was SIGTERM'd. The shutdown completed in 100ms with **no drain** — every in-flight cron run was killed mid-call, including stateful sequencer touches and the Outbound Sprint. Telegram fired one alert per killed run instead of one digest. This is the third such cascade in a week; the root cause is recurring anthropic-billing exhaustion driving repeated doctor runs, and the gateway's fast-shutdown path bypassing the drain primitive that already exists for in-process restarts. Fix is layered: (1) suppress the Telegram noise on known restart events (Rick-owned, low cost), (2) ask upstream to route LaunchAgent-triggered SIGTERMs through the existing drain path (upstream-owned), (3) add an anthropic-billing circuit-breaker so doctor stops being the recovery hammer (upstream-owned). Stateful crons should checkpoint per-item — but that's a per-handler refactor and is the slowest of the three.

---

## Part A — Diagnosis

`gateway.log` window 01:52–01:54 PT shows:

- `01:52:21` live model switch mid-attempt: `openai/gpt-5.4-mini -> openai/gpt-5.4`. Anthropic was already in fallback before doctor ran. In-flight runs were already degraded.
- `01:53:20.442` `[gateway] signal SIGTERM received`
- `01:53:20.470` `received SIGTERM; shutting down`  ← **no `draining N active task(s)`** line; compare 2026-05-04T03:38:15 which logged `draining 2 active task(s) and 1 active embedded run(s) before restart with timeout 300000ms`. The drain primitive **exists** but was not invoked here.
- `01:53:20.605` `completed cleanly in 100ms` — too fast to be a drain; this is a kill-and-exit path.
- `01:53:24` gateway re-launched by LaunchAgent and was healthy by `01:53:26.172`.

**Conclusions:**

1. **Restart was not strictly required.** Doctor's plist replacement triggers `launchctl bootout`/`bootstrap`, which already restarts the gateway. The explicit `gateway restart` call inside `doctor --fix` is redundant on macOS LaunchAgent installs and adds a second SIGTERM round-trip.
2. **The kill path is "stop", not "drain".** The 100ms shutdown indicates a `restart mode: hard` style code path. The drain code path observed on 05-04 is wired for in-process supervisor restarts, not LaunchAgent-triggered ones.
3. **Cron failure-handler emits per-job Telegram alerts** with reason "interrupted by gateway restart" — no debouncing, no awareness that a restart is global.
4. **Anthropic was already down** before the restart; some runs were stuck on degraded fallbacks. The restart did not *cause* model unavailability — it amplified existing pain.

`~/.openclaw/cron/jobs.json` has 180 cron entries. Roughly half are disabled. The cascade affected the enabled stateful set.

## Part B — Failure-Mode Classification

| Cron type | Examples | Restart impact | Action needed |
|---|---|---|---|
| **Idempotent / interval** | Email check (15min), Heartbeat (30min), feed-poll, sentry-poll | Self-heals at next tick | None — but suppress the alert |
| **Stateful / batch** | Multi-Channel Outbound Sprint (2x daily), Email sequencer touches, Roast Lead Follow-up | Aborts mid-batch; on retry either re-sends already-sent items or re-starts from item 0 | Per-item checkpoint (Part C #1) |
| **Long-running model-grind** | Deep-research sessions, content-factory, prompt-evolution | Most damaging — N model calls discarded, partial output lost, anthropic spend wasted | Drain on shutdown OR checkpoint per LLM call |

The Outbound Sprint and Roast Lead Follow-up are the priority — they touch external state (sends emails / DMs) where re-running from scratch is not free.

## Part C — Resilience Patterns (ranked)

### 1. Per-item checkpoint files — HIGH cost, HIGH gain, owner: Rick (per-handler)

Each stateful cron writes `~/.openclaw/cron/state/<name>.json` after each item processed: `{cursor, processed_ids[], started_at, last_item_at}`. On startup, the handler reads its checkpoint, skips processed_ids, resumes from cursor. On clean completion, file is deleted. Reliability gain for stateful crons: ~95%. Cost: each handler needs a 10–20 line wrap. Do not generalize prematurely; start with Outbound Sprint and the sequencer touches.

### 2. Gateway pre-restart drain hook — MED cost, MED-HIGH gain, owner: upstream OpenClaw

The drain path already exists (logged 2026-05-04T03:38:15). The ask: route *all* SIGTERM paths — including LaunchAgent bootout — through `gateway.drain(timeout=120s)` before exit. This solves both stateful and long-grind classes without per-handler changes. Vlad cannot implement this; file an OpenClaw issue.

### 3. Telegram error suppression for restart events — LOW cost, LOW gain (signal hygiene only), owner: Rick

When cron failure reason matches `/interrupted by gateway restart|SIGTERM/`, suppress per-job alert and instead emit one digest from the gateway's post-startup hook: *"Gateway restarted at HH:MM. N crons interrupted, M idempotent (will self-recover next tick), K stateful (need manual re-fire: …)."* This is notification UX only — it does not save state. But it stops Vlad from chasing 6 false alarms per restart.

## Part D — Doctor Procedure (5-step runbook)

When `openclaw doctor --fix` is the right call:

1. **Pre-flight:** `openclaw sessions list --filter status=processing` → cancel anything stuck >30min. Owner: **Vlad**.
2. **Drain:** there is no `openclaw cron pause` primitive today. Best available substitute: `openclaw cron list --enabled` to capture the current enabled set, then accept that 8–10 in-flight runs may be killed. Owner: **upstream** to add `cron pause`; **Vlad** notes the gap.
3. **Run doctor:** `openclaw doctor --fix`. Owner: **Vlad**.
4. **Post-restart verify:** `openclaw cron status` and `openclaw cron runs --since 5m`. Re-fire any stateful cron that needs it: `openclaw cron run <id>`. Owner: **Vlad** (manual approval).
5. **Telegram silence:** set a 5-minute silence flag if available; today the equivalent is "ignore the next 6 alerts." Owner: **Rick** to add a silence flag in the alert sender.

## Part E — Anthropic Billing Circuit-Breaker (owner: upstream OpenClaw, with Rick-side fallback)

The doctor cascade is downstream of anthropic billing exhaustion. Recommendation:

- The gateway already has a `cooldown:billing` state for anthropic. When cooldown duration exceeds **2 hours**, the gateway should:
  1. Switch all routes preferring anthropic to the fallback config (gpt-5.4 for reasoning paths; **never gpt-5.4-mini for personalization** per the smart-models invariant).
  2. Emit **one** Telegram summary: *"anthropic down >2h, fallback to gpt-5.4 for the duration; N enabled crons running on fallback."*
  3. Resume scheduled crons normally; do not require Vlad to top up before crons run.
- This decouples cron health from billing top-up timing and removes the doctor-as-recovery loop.
- Vlad-side fallback: a billing-watchdog plist already exists (`ai.rick.anthropic-billing-watchdog.plist`). Confirm it routes to fallback rather than emitting "billing dead — please top up."

## Next concrete action

File one upstream OpenClaw issue titled *"LaunchAgent-triggered SIGTERM bypasses gateway drain (cron cascade-kill)"* with the 01:53:20 log excerpt and the 05-04T03:38:15 drain-path comparison — this is the single highest-leverage fix and only upstream can make it.

Concurrently (Rick-owned): the smart-models-invariant violation in `~/.openclaw/cron/jobs.json` (97 enabled+disabled jobs hardcoded to `openai/gpt-5.4-mini`, 2 to retired `gpt-4o-mini`, 1 to vague `opus`) is being addressed in a separate surgical pass — see follow-up commit.
