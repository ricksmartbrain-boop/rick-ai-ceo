# OPENCLAW_SETUP.md — Rick v6 On Mac Studio

This is the concrete deployment path for your dedicated Mac Studio.

## 1. Install Core Prerequisites

If Homebrew is missing, install Homebrew first.

```bash
brew install node@22 python@3.12 tmux jq gh himalaya
npm install -g pnpm openclaw@latest ralphy-cli @openai/codex @anthropic-ai/claude-code vercel
brew install stripe/stripe-cli/stripe
```

Then confirm:

```bash
node --version
python3 --version
openclaw --version
codex --version
claude --version
```

Provider safety before going further:
- use official APIs and official CLI auth only
- do not automate consumer Claude or Gemini web apps
- stay inside supported regions
- do not try to bypass quotas, bans, or guardrails
- read `PROVIDER_SAFETY.md`

## 2. Install OpenClaw

```bash
openclaw onboard --install-daemon
openclaw status
openclaw doctor
```

Expected result:
- `~/clawd` exists
- OpenClaw daemon is running
- the local gateway is healthy

## 3. Install Rick v6 Into The OpenClaw Workspace

From this repo:

```bash
bash rick-v6/scripts/install-openclaw-workspace.sh --target ~/clawd --force
bash ~/clawd/scripts/setup.sh --yes
```

Then read `~/clawd/START_HERE.md` before continuing.

## 4. Configure Rick

```bash
cp ~/clawd/config/rick.env.example ~/clawd/config/rick.env
```

Then fill:
- domains
- Stripe
- Beehiiv
- LinkedIn
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- `XAI_API_KEY`
- strategy-panel model ids if you want to override the defaults
- `config/model-pricing.json` so spend estimation matches your real pricing
- Telegram bot token
- Telegram allowed chat id
- if you want thread mode, also set:
  - `RICK_TELEGRAM_THREAD_MODE=hybrid`
  - `RICK_TELEGRAM_FORUM_CHAT_ID`
  - `config/telegram-topics.json`
- review and keep aligned:
  - `config/openclaw-session-policy.json`
  - `config/openclaw-agent-blueprint.json`
  - `templates/openclaw/memory-flush.prompt.md`
  - `OPENCLAW_PROFILE.md`
- portfolio, approval, lane-policy, and watchdog-process configs
- the placeholder control files under `~/rick-vault/control/`

Before going live, authenticate the CLIs you intend to use:
- `gh auth login`
- `vercel login`
- `stripe login`
- `codex auth login`
- run `claude` once and complete Claude Code authentication
- configure Himalaya in `~/.config/himalaya/config.toml`
- set `GOOGLE_API_KEY` or `GEMINI_API_KEY` from an official Gemini API project

Recommended model posture on day one:
- strategy / planning: `gpt-5.4-pro` plus the strategy panel with `claude-opus-4-6` and `gemini-3.1-pro-preview`
- hard coding: `gpt-5.4-pro`
- coding fallback specialist: `claude-opus-4-6`
- repo/git automation throughput: `gpt-5.3-codex`
- daily operator execution and writing: `claude-sonnet-4-6`
- analysis and context synthesis: `gemini-3.1-pro-preview`
- heartbeat and recurring parsers: `gemini-3.1-flash-lite-preview`

Optional later, not required before launch:
- local/private support stack with `Qwen3-Coder`, `Qwen3`, `Qwen3Guard`, `Qwen3 Embedding`, `Kimi-Dev-72B`, `DeepSeek-V3.2`, or `MiniMax-M1`
- keep those as review/rerank/guardrail lanes until the first revenue loop is proven

## 5. Run Preflight And Bootstrap

```bash
bash ~/clawd/scripts/preflight-openclaw.sh
bash ~/clawd/scripts/bootstrap.sh
bash ~/clawd/scripts/doctor.sh
python3 ~/clawd/runtime/runner.py status
```

If thread mode is enabled, use a forum-enabled Telegram supergroup and bootstrap the standard topics:

```bash
python3 ~/clawd/runtime/runner.py telegram-topics bootstrap
python3 ~/clawd/runtime/runner.py telegram-topics list
```

Telegram forum setup order:
- create or convert the founder-control Telegram supergroup into a forum
- set `RICK_TELEGRAM_ALLOWED_CHAT_ID` and `RICK_TELEGRAM_FORUM_CHAT_ID` to that supergroup id
- run `telegram-topics bootstrap`
- send `/status` from `CEO HQ`
- send `/queue "First offer" --price 29 --type guide` from `Product Lab`
- confirm that bound workflows expose both `telegram_target` and `openclaw_session_key`

## 6. Smoke-Test The Gold Path

```bash
python3 ~/clawd/runtime/runner.py queue-info-product --idea "Autonomous Revenue OS" --price-usd 29 --product-type guide
python3 ~/clawd/runtime/runner.py work --limit 20
python3 ~/clawd/runtime/runner.py status
```

## 7. Always-On Operation

Use OpenClaw plus Rick's runtime loop:
- OpenClaw daemon for wakeups, channels, events
- one active OpenClaw agent now: `rick`
- `bash ~/clawd/scripts/run-daemon.sh` in tmux or launchd for continuous queue progress
- `bash ~/clawd/scripts/run-telegram-bridge.sh` in tmux or launchd for founder-control polling
- Telegram as founder control surface
- in thread mode, approvals, failures, publish events, and workflow status stay in the workflow topic unless no workflow topic exists
- keep secure customer/support Telegram DMs in `prepared` mode until launch stability is proven
- future `rick-ceo`, `rick-builder`, `rick-distribution`, and `rick-customer-ops` stay documented in `config/openclaw-agent-blueprint.json`; do not activate them on day one
- launchd templates are included under `deploy/launchd/`
- cron installer is included under `scripts/install-crons.sh`

## 8. Final Checklist

- `openclaw status` is healthy
- `bash ~/clawd/scripts/preflight-openclaw.sh` returns ready
- `bash ~/clawd/scripts/doctor.sh` has no critical gaps
- `python3 ~/clawd/runtime/runner.py status` works
- Telegram bridge is running and `/status` reaches Rick
- one info-product workflow reaches `launch-ready`
