# Mac Studio Deployment Runbook

Step-by-step guide to deploy Rick v6 on your Mac Studio. Follow in order.

---

## Phase 1: Prerequisites (10 min)

### Install system tools

```bash
# Homebrew (skip if already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Core dependencies
brew install node@22 python@3.12 tmux jq gh himalaya
brew install stripe/stripe-cli/stripe

# Global npm packages
npm install -g pnpm openclaw@latest ralphy-cli @openai/codex @anthropic-ai/claude-code vercel
```

### Verify everything installed

```bash
node --version      # Should be v22+
python3 --version   # Should be 3.12+
openclaw --version
tmux -V
jq --version
```

---

## Phase 2: Setup Rick (5 min)

```bash
cd ~/Desktop/Felix-OpenClaw/rick-v6

# Run the interactive setup (installs Python deps, copies templates, seeds config)
bash scripts/setup.sh --yes
```

This will:
- Install Python packages from `requirements.txt`
- Create `config/rick.env` from the example template
- Copy integration credential templates to `~/.config/`
- Install `xpost` binary
- Create `~/rick-vault/` directory structure
- Install cron jobs

---

## Phase 3: Configure (15-30 min)

Edit `config/rick.env` with your real values:

### Required API keys

```bash
# LLM providers (at minimum, set OpenAI + one fallback)
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."
GOOGLE_API_KEY="AI..."        # or GEMINI_API_KEY
XAI_API_KEY="xai-..."
```

### Required for Telegram control

```bash
RICK_TELEGRAM_BOT_TOKEN="123456:ABC..."
RICK_TELEGRAM_ALLOWED_CHAT_ID="-100..."
```

How to get these:
1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Create a private group, add the bot as admin
3. Send a message in the group, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find the chat ID (negative number starting with `-100`)

### Required for revenue

```bash
STRIPE_SECRET_KEY="sk_live_..."
RICK_PRIMARY_DOMAIN="https://yourdomain.com"
```

### Recommended for full functionality

```bash
BEEHIIV_API_KEY="..."               # Newsletter
BEEHIIV_PUB_ID="..."
LINKEDIN_ACCESS_TOKEN="..."         # Social posting
RICK_X_HANDLE="@yourhandle"         # X/Twitter
```

### Daily LLM spend cap

```bash
RICK_LLM_DAILY_CAP_USD="50"        # Default $50/day, 0 = unlimited
```

### Authenticate CLIs

```bash
gh auth login
vercel login
stripe login
# Run claude once to complete auth:
claude --version
```

---

## Phase 4: Verify (5 min)

```bash
# Check for missing config, broken deps, placeholder issues
bash scripts/doctor.sh

# Should show runtime state (empty workflows is fine)
python3 runtime/runner.py status

# If doctor.sh reports issues, fix them before proceeding
```

Common doctor.sh fixes:
- "API key empty" → fill the key in `config/rick.env`
- "placeholder found" → replace `[TODO]` markers in `~/rick-vault/control/*.md`
- "binary not found" → install the missing tool via brew/npm

---

## Phase 5: Deploy (choose one approach)

### Option A: Cron-first (recommended for day 1)

```bash
# Install 4 cron jobs: heartbeat (30 min), nightly, weekly, inbox
bash scripts/install-crons.sh

# Verify
crontab -l | grep RICK_CRON

# Start Telegram bridge in tmux
tmux new -s rick-telegram
bash scripts/run-telegram-bridge.sh
# Press Ctrl+B, D to detach
```

### Option B: Daemon + launchd (production, survives reboots)

```bash
# Install launchd services (replaces placeholders with your paths)
bash scripts/install-launchd.sh

# Activate
launchctl load ~/Library/LaunchAgents/ai.rick.daemon.plist
launchctl load ~/Library/LaunchAgents/ai.rick.telegram-bridge.plist

# Verify running
launchctl list | grep ai.rick

# Check logs
tail -f ~/rick-vault/logs/daemon.log
```

To stop:
```bash
launchctl unload ~/Library/LaunchAgents/ai.rick.daemon.plist
launchctl unload ~/Library/LaunchAgents/ai.rick.telegram-bridge.plist
```

---

## Phase 6: First Revenue Loop

### Test via Telegram

Send these in your Telegram group:

```
/status                                          # Verify Rick responds
/queue "My First Guide" --price 29 --type guide  # Queue a product
/status                                          # See the workflow
```

### Or via CLI

```bash
python3 runtime/runner.py queue-info-product \
  --idea "Autonomous Revenue OS" \
  --price-usd 29 \
  --product-type guide

# Process jobs
python3 runtime/runner.py work --limit 20

# Check progress
python3 runtime/runner.py status
```

### What happens next

Rick will work through the gold path:
1. Compile context
2. Write research brief
3. Write offer brief
4. Build outline
5. Scaffold product + Stripe metadata
6. Generate landing page
7. Generate newsletter draft
8. Generate social package
9. **Request your approval** (via Telegram)
10. You `/approve <id>` and Rick marks it `launch-ready`
11. Publish to newsletter, LinkedIn, X

`launch-ready` will **block** if the checkout URL is fake or placeholder. You need a real Stripe payment link or waitlist endpoint.

---

## Monitoring & Troubleshooting

### Check status

```bash
# System status
python3 runtime/runner.py status

# Daemon health
tail -50 ~/rick-vault/logs/daemon.log

# LLM spend today
cat ~/rick-vault/operations/llm-usage.jsonl | grep "$(date +%Y-%m-%d)" | python3 -c "
import sys, json
total = sum(json.loads(l).get('usd', 0) for l in sys.stdin)
print(f'Today: \${total:.2f}')
"

# launchd status
launchctl list | grep ai.rick
```

### Common issues

| Symptom | Fix |
|---------|-----|
| Telegram `/status` no response | Check `RICK_TELEGRAM_BOT_TOKEN` and `RICK_TELEGRAM_ALLOWED_CHAT_ID` in `rick.env`. Restart bridge. |
| `python3: No module named requests` | Run `python3 -m pip install --user -r requirements.txt` |
| `doctor.sh` shows placeholder warnings | Edit `~/rick-vault/control/*.md` and replace `[TODO]` markers |
| Daemon not starting via launchd | Check `~/rick-vault/logs/rick-daemon.err.log`. Usually a missing PATH issue — verify plist has Homebrew in PATH. |
| LLM calls returning fallback text | Check API keys in `rick.env`. Run `bash scripts/doctor.sh` to verify. |
| `SQLITE_BUSY` errors | Should not happen with busy_timeout=5000. If persistent, check for zombie processes: `ps aux | grep runner.py` |

### Logs location

```
~/rick-vault/logs/
├── daemon.log                    # Main daemon output (rotates at 50MB)
├── rick-daemon.out.log           # launchd stdout
├── rick-daemon.err.log           # launchd stderr
├── rick-telegram-bridge.out.log  # Telegram bridge stdout
├── rick-telegram-bridge.err.log  # Telegram bridge stderr
└── cron/                         # Cron job logs
    ├── heartbeat.log
    ├── nightly.log
    ├── weekly.log
    └── inbox.log
```

---

## Upgrade Path

When updating Rick v6 code:

```bash
cd ~/Desktop/Felix-OpenClaw/rick-v6

# Stop services
launchctl unload ~/Library/LaunchAgents/ai.rick.daemon.plist 2>/dev/null
launchctl unload ~/Library/LaunchAgents/ai.rick.telegram-bridge.plist 2>/dev/null

# Pull changes
git pull

# Re-run setup (safe to re-run, idempotent)
bash scripts/setup.sh --yes

# Verify
bash scripts/doctor.sh

# Restart
launchctl load ~/Library/LaunchAgents/ai.rick.daemon.plist
launchctl load ~/Library/LaunchAgents/ai.rick.telegram-bridge.plist
```

---

## Security Notes

- `config/rick.env` contains all secrets — it is gitignored, never commit it
- Log files may contain LLM prompts/responses — `~/rick-vault/logs/` is chmod 700
- API keys are sent via HTTP headers (never in URLs)
- The SQLite DB at `~/rick-vault/runtime/rick-runtime.db` contains workflow state, not secrets
- Telegram bridge only responds to `RICK_TELEGRAM_ALLOWED_CHAT_ID`
