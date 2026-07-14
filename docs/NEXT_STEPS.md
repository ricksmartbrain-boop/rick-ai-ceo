# NEXT_STEPS.md — Strategic Path Forward

This is the recommended order for taking Rick from a strong runtime base to a genuinely live, revenue-producing autonomous operator.

## Core Principle

Do not widen Rick faster than you harden him.

The right path is:
- first make one money loop brutally reliable
- then make support and recovery reliable
- then widen the surface area

## Phase 1 — Go Live Cleanly

Goal:
- get Rick running on the dedicated Mac Studio with correct access, guardrails, and founder control

Do next:
1. Fill `config/rick.env` with real credentials and paths.
2. Fill the founder control files in `~/rick-vault/control/`.
3. Review and keep aligned:
   - `config/openclaw-session-policy.json`
   - `config/openclaw-agent-blueprint.json`
   - `templates/openclaw/memory-flush.prompt.md`
   - `OPENCLAW_PROFILE.md`
4. Fill `config/watchdog-processes.json` with the real daemon, Telegram bridge, and any tmux agent sessions that Rick may restart safely.
5. Install into `~/clawd`.
6. If thread mode is enabled, bootstrap the forum topics and verify session-key visibility with `python3 runtime/runner.py status`.
7. Keep only the main `rick` agent active. Do not turn on the specialist blueprint yet.
8. Start:
   - `bash scripts/run-daemon.sh`
   - `bash scripts/run-telegram-bridge.sh`
9. Run:
   - `bash scripts/preflight-openclaw.sh`
   - `bash scripts/doctor.sh`
   - `bash scripts/guardrails-audit.sh`

Success looks like:
- Telegram `/status` works
- bound workflows show both `telegram_target` and `openclaw_session_key`
- guardrails audit is mostly `pass`
- watchdog report is live
- memory overview is live
- one queued workflow moves normally through the runtime

## Phase 2 — Harden One Canonical Revenue Loop

Goal:
- make Rick capable of repeatedly shipping and monetizing one info product path

Do next:
1. Pick the first canonical offer.
2. Define the exact:
   - landing page
   - checkout path
   - delivery path
   - newsletter launch
   - social package
   - support response path
3. Test the loop end to end with a real purchase and delivery.
4. Refuse expansion until this loop is reliable.

Must-have criteria:
- no fake checkout links
- no fake publish confirmations
- fulfillment happens after payment
- support questions create memory
- post-purchase drafts appear in the outbox on schedule
- Rick can explain where the workflow is blocked at any moment

Why this is first:
- Felix felt alive because one revenue path was real
- without this, Rick is still an operator console with nice runtime structure

## Phase 3 — Close The Reliability Gaps

Goal:
- remove the last places where Rick still depends on soft policy instead of enforced behavior

Highest-value builds:
1. Build/test enforcement for coding loops
   - every critical coding/deploy flow should verify build or tests before success
2. Sentry ingestion -> runtime job pipeline
   - errors should become prioritized fix workflows
3. Live email send bridge
   - turn drafted outbox mail into provider sends with policy gating
   - keep fortress triage as the decision layer
4. Recovery verification
   - after watchdog restarts something, verify the service is really healthy

Success looks like:
- fewer false positives
- safer coding autonomy
- fewer silent failures
- better founder trust

## Phase 4 — Make Rick Self-Sustaining

Goal:
- push Rick beyond launch into retention, support, and compounding product learning

Build next:
1. Post-purchase fulfillment runtime
2. Customer memory namespaces
   - objections
   - refund reasons
   - use cases
   - testimonial capture
3. Richer follow-up email and onboarding loops
4. Upsell and next-offer recommendation logic

Why this matters:
- launch-only agents are fragile
- self-sustaining agents learn from customers and improve future revenue loops
- Rick now has a first fulfillment path; the next job is to make it richer and connected to real delivery/sending surfaces

## Phase 5 — Add Feedback-Driven Growth

Goal:
- let Rick allocate time and capital based on real signals, not vibes

Build next:
1. Channel analytics ingestion
   - newsletter
   - social
   - landing page
   - checkout
2. Experiment registry
   - hypothesis
   - stop threshold
   - scale threshold
   - result
3. Distribution graph
   - one artifact -> many channels and reuse patterns
4. Portfolio allocator upgrades
   - kill weak initiatives fast
   - double down on what converts

Success looks like:
- Rick knows which channel deserves effort
- Rick knows which offer to kill
- Rick improves output from evidence, not guesswork

## Phase 6 — Build Evidence-Driven Autonomy

Goal:
- make Rick smarter by evidence and calibration, not by adding uncontrolled autonomy

Build next:
1. Evidence graph
   - source
   - timestamp
   - confidence
   - verification state
2. Calibration ledger
   - prediction
   - confidence
   - actual outcome
   - model/lane attribution
3. Shadow launch twin
   - simulate broken CTA, checkout, support surge, and missing assets before launch
4. Founder twin
   - learn approval preferences and memo style without bypassing founder control

Success looks like:
- Rick gets more accurate over time
- Rick routes work based on measured performance, not vibes
- launch mistakes are caught before going public
- founder time per launch decreases without losing control

## Do Not Prioritize Yet

Avoid these until the earlier phases are solid:
- too many simultaneous products
- recursive multi-agent spawning
- speculative token / coin launches
- heavy brand expansion before one core loop sells reliably
- fancy dashboards without operational decisions tied to them
- swapping core cloud models for local models before the revenue loop is proven

## Best Immediate Next Build

If choosing only one implementation step after deployment:

1. live email send bridge

Why:
- Rick already drafts lifecycle and fulfillment mail
- the missing step is trusted delivery through the actual provider
- this closes one of the last gaps between “prepared runtime” and “real operating agent”

## Short Version

Priority order:
1. deploy Rick cleanly on the Mac Studio
2. harden one info-product revenue loop
3. ship the live email send bridge and enforce build/test
4. deepen customer memory and incident recovery
5. add analytics and experiment-driven allocation
6. add evidence graph, calibration, and shadow-launch intelligence
