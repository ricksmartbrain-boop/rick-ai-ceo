# START_HERE.md — Founder Setup Order

This is the shortest path to turning `rick-v6` into a live Rick agent on OpenClaw.

## 1. Fill The Inputs Rick Needs

Copy and complete these first:

1. `config/rick.env.example` -> `config/rick.env`
2. `$RICK_DATA_ROOT/control/founder-profile.md`
3. `$RICK_DATA_ROOT/control/access-inventory.md`
4. `$RICK_DATA_ROOT/control/heartbeat-targets.md`
5. `$RICK_DATA_ROOT/control/launch-playbook.md`
6. `config/watchdog-processes.json`
7. `config/model-pricing.json`
8. if `RICK_TELEGRAM_THREAD_MODE` is not `off`, also fill:
   - `RICK_TELEGRAM_FORUM_CHAT_ID`
   - `config/telegram-topics.json`
9. OpenClaw profile files:
   - `config/openclaw-session-policy.json`
   - `config/openclaw-agent-blueprint.json`
   - `templates/openclaw/memory-flush.prompt.md`
10. `~/.config/openclaw/health-targets.conf`
11. one real launch path:
   - Stripe payment link in each product's `stripe-product.json`, or
   - `RICK_DEFAULT_WAITLIST_API` / workflow `waitlist_api`
12. verify the outbox path Rick will use for lifecycle mail:
   - `mailbox/outbox/`
   - post-purchase drafts land here before provider send

Critical envs for the live model layer:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- `XAI_API_KEY`
- optional gateway: `RICK_LLM_GATEWAY_URL` and `RICK_LLM_GATEWAY_API_KEY`

Provider safety:
- use official API keys and official CLI auth only
- do not automate `claude.ai` or consumer Gemini web surfaces
- keep the Mac Studio in a supported region for the providers you use
- read `PROVIDER_SAFETY.md`

Recommended first-day model posture:
- strategy / planning / board thinking: `gpt-5.4-pro` with the bounded strategy panel
- hard coding: `gpt-5.4-pro`
- coding specialist fallback: `claude-opus-4-6`
- repo/git automation throughput: `gpt-5.3-codex`
- daily execution and writing: `claude-sonnet-4-6`
- analysis and memory synthesis: `gemini-3.1-pro-preview`
- heartbeat and recurring parsers: `gemini-3.1-flash-lite-preview`

Model-spend truth:
- review `config/model-pricing.json`
- keep it aligned with your real provider pricing and the exact model ids your accounts expose

Rick can run without every field complete, but any remaining `[TODO]` markers should be treated as real operating gaps.
Rick should also treat placeholder launch URLs as real operating gaps. `launch-ready` now blocks unless checkout or waitlist capture is real.
Keep `RICK_OPENCLAW_SECURE_DM_MODE=prepared` for the first deployment. Founder-control Telegram is the only active Telegram surface at launch.

## 2. Customize The Core Persona Files

Edit these workspace files:

- `IDENTITY.md`
  - mission
  - revenue target
  - business portfolio
  - public-building posture
- `AGENTS.md`
  - CLI/auth inventory
  - sensitive inputs
  - memory roots if different
- `HEARTBEAT.md`
  - real monitoring targets
  - real escalation rules
  - founder response expectations
- `TOOLS.md`
  - actual model/tool routing
  - coding agent preferences
  - social/newsletter surfaces in use
- `MEMORY.md`
  - confirm the Rick Vault is the canonical long-term context layer

## 3. Run The Setup Checks

```bash
bash scripts/setup.sh --yes
node --version
python3 --version
openclaw --version
codex --version
claude --version
bash scripts/preflight-openclaw.sh
bash scripts/bootstrap.sh
bash scripts/doctor.sh
python3 runtime/runner.py status
```

If `doctor.sh` reports missing config, missing CLIs, or unfinished placeholders, fix those before going live.
If `RICK_TELEGRAM_THREAD_MODE` is enabled, bootstrap the fixed forum topics too:

```bash
python3 runtime/runner.py telegram-topics bootstrap
python3 runtime/runner.py telegram-topics list
python3 runtime/runner.py status
```

What to confirm:
- bound workflows show both `telegram_target` and `openclaw_session_key`
- `openclaw_session_key` uses the `agent:rick:telegram:group:<chat_id>:topic:<thread_id>` pattern
- only the main `rick` agent is active now
- future specialist agents stay documented only in `config/openclaw-agent-blueprint.json`

## 4. Install Into OpenClaw

```bash
bash scripts/install-openclaw-workspace.sh --target ~/clawd --force
```

Then in `~/clawd`:

```bash
bash scripts/preflight-openclaw.sh
bash scripts/bootstrap.sh
bash scripts/doctor.sh
```

## 5. Start Rick

Run these on the Mac Studio:

```bash
bash scripts/run-daemon.sh
bash scripts/run-telegram-bridge.sh
```

Optional reboot-safe deployment:
- use the `deploy/launchd/` templates
- or install `bash scripts/install-crons.sh` for Felix-style recurring jobs
- keep only the main `rick` agent active until the first revenue loop is stable

## 6. First Live Founder Commands

Once Telegram is connected:

- `/help`
- `/status`
- `/lanes`
- `/queue "First offer name" --price 29 --type guide`
- `/bind here wf_example` from a project-specific topic if you manually create one
- `/unbind here` to detach a workflow from the current project topic
- `python3 runtime/runner.py record-purchase --workflow-id wf_example --email buyer@example.com --delivery-url https://deliver.rick.ai/example`

## 7. First Real Goal

Do not start with ten products.

Start with one hardened loop:
- one info product
- one landing page
- one payment path
- one newsletter launch
- one social distribution package
- one post-purchase delivery path
- one support + follow-up path

The broader mission stays constant:
- build toward `$100K MRR` through our products
- preserve durable memory and context in the Rick Vault
