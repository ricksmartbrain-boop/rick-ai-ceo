# Rick v6 — Autonomous Revenue Agent

Rick v6 is an always-on AI agent that builds and ships digital products toward **$100K MRR**. It runs on a dedicated Mac Studio with SQLite persistence, multi-provider LLM routing, Telegram founder control, and OpenClaw integration.

## What Rick Does

1. **Queues product ideas** via Telegram or CLI
2. **Executes a gold path**: research → offer → outline → scaffold → landing page → newsletter → social → approval → publish
3. **Processes payments** via Stripe with real checkout enforcement (blocks placeholder URLs)
4. **Delivers products** with post-purchase fulfillment, customer memory, and follow-up sequences
5. **Reports status** through Telegram with approval gates for irreversible actions

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Mac Studio (always-on)                                 │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐ │
│  │ Daemon   │  │ Telegram     │  │ Cron (heartbeat,  │ │
│  │ (120s)   │  │ Bridge       │  │  nightly, weekly) │ │
│  └────┬─────┘  └──────┬───────┘  └────────┬──────────┘ │
│       │               │                   │             │
│       └───────────────┼───────────────────┘             │
│                       │                                 │
│              ┌────────▼────────┐                        │
│              │  SQLite Runtime │  (workflows, jobs,     │
│              │  rick-runtime.db│   approvals, artifacts)│
│              └────────┬────────┘                        │
│                       │                                 │
│    ┌──────────────────┼──────────────────┐              │
│    │                  │                  │              │
│    ▼                  ▼                  ▼              │
│  OpenAI           Anthropic          Google/xAI         │
│  (strategy,       (writing,          (analysis,         │
│   coding)          review)           heartbeat,research)│
│                                                         │
│  ~/rick-vault/  ← Obsidian memory (PARA structure)      │
└─────────────────────────────────────────────────────────┘
```

## Quick Start (Mac Studio)

See **[SETUP.md](SETUP.md)** for the complete deployment runbook. The short version:

```bash
# 1. Install deps
brew install node@22 python@3.12 tmux jq gh himalaya
brew install stripe/stripe-cli/stripe

# 2. Setup Rick
bash scripts/setup.sh --yes

# 3. Configure
# Edit config/rick.env with your real API keys and domains

# 4. Verify
bash scripts/doctor.sh
python3 runtime/runner.py status

# 5. Deploy (cron-first approach, recommended)
bash scripts/install-crons.sh

# 6. Start Telegram bridge
bash scripts/run-telegram-bridge.sh

# 7. Queue first product
# In Telegram: /queue "My First Guide" --price 29 --type guide
```

## Deployment Modes

| Mode | Command | Use When |
|------|---------|----------|
| **Cron** (recommended first) | `bash scripts/install-crons.sh` | Starting out — heartbeat every 30 min |
| **Daemon** | `bash scripts/run-daemon.sh` | After cron is stable — continuous 120s loop |
| **launchd** (survives reboots) | `bash scripts/install-launchd.sh` | Production — auto-start on boot |
| **Telegram bridge** | `bash scripts/run-telegram-bridge.sh` | Always — founder control surface |

## Telegram Commands

| Command | What it does |
|---------|-------------|
| `/help` | Show available commands |
| `/status` | Current workflows, lanes, spend |
| `/lanes` | Lane utilization |
| `/queue "idea" --price 29 --type guide` | Queue a new info product |
| `/work 3` | Process up to 3 jobs |
| `/approve <id> note` | Approve a pending action |
| `/deny <id> note` | Deny a pending action |
| `/publish <wf_id> newsletter,linkedin,x` | Publish to channels |

## LLM Routing

| Route | Default Model | Purpose |
|-------|--------------|---------|
| `strategy` | gpt-5.4 | Planning, decisions (strategy panel: 3 models) |
| `coding` | gpt-5.4-pro | Code generation |
| `writing` | claude-sonnet-4-6 | Content, marketing copy |
| `review` | claude-opus-4-6 | Red-team, QA |
| `analysis` | gemini-3.1-pro-preview | Data, context synthesis |
| `heartbeat` | gemini-3.1-flash-lite-preview | Lightweight status checks |
| `research` | grok-4-latest | Web research, fact-checking |

Daily spend cap: `$50/day` (configurable via `RICK_LLM_DAILY_CAP_USD`). Heartbeat is always exempt.

## Key Files

```
rick-v6/
├── config/
│   ├── rick.env.example        # All configuration (copy to rick.env)
│   ├── model-pricing.json      # Per-model cost rates
│   ├── lane-policy.json        # Concurrent lane limits
│   └── approval-policy.json    # What needs founder approval
├── runtime/
│   ├── runner.py               # CLI entry point
│   ├── engine.py               # Workflow state machine
│   ├── db.py                   # SQLite persistence (WAL mode)
│   ├── llm.py                  # Multi-provider LLM router
│   └── telegram_bridge.py      # Telegram poller
├── scripts/
│   ├── setup.sh                # First-run bootstrap
│   ├── doctor.sh               # Health check
│   ├── install-crons.sh        # Felix-style cron jobs
│   ├── install-launchd.sh      # macOS service install
│   ├── run-daemon.sh           # Always-on loop
│   └── run-telegram-bridge.sh  # Telegram listener
├── skills/                     # 31+ modular capabilities
├── deploy/launchd/             # Plist templates
├── templates/                  # Vault structure, OpenClaw config
└── ~/rick-vault/               # Obsidian memory (created by bootstrap)
```

## Safety & Guardrails

- **Approval gates**: Irreversible actions (spend, publish, delete) require `/approve` via Telegram
- **Launch enforcement**: `launch-ready` blocks unless checkout URL or waitlist is real (no placeholders)
- **Daily LLM budget**: Hard cap prevents runaway API spend
- **SQLite WAL + busy_timeout**: Safe concurrent access across daemon, bridge, and cron
- **Watchdog**: Auto-restart with cooldowns and daily caps

## Documentation

| Doc | Purpose |
|-----|---------|
| **[SETUP.md](SETUP.md)** | Step-by-step Mac Studio deployment |
| **[IDENTITY.md](IDENTITY.md)** | Rick's operating mandate |
| **[ANALYSIS.md](ANALYSIS.md)** | Felix comparison, what v6 fixes |
| **[HEARTBEAT.md](HEARTBEAT.md)** | Monitoring and escalation |
| **[ANTI_PATTERNS.md](ANTI_PATTERNS.md)** | Failure modes to avoid |
| **[PROVIDER_SAFETY.md](PROVIDER_SAFETY.md)** | API usage compliance |

## Current Status

**Ready for Mac Studio deployment.** All deployment blockers resolved:
- Python deps auto-installed during setup
- Daemon logs to file (no silent failures)
- SQLite concurrent access protected
- LLM daily spend cap enforced
- launchd installer with proper PATH and crash protection
- `.gitignore` prevents secret commits
