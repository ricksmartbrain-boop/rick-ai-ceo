# Upwork Skill

Manages Rick's Upwork freelancer presence — job scanning, proposal writing, contract fulfillment, client communication, and revenue tracking. Uses a swarm architecture: Remy scouts jobs, Teagan writes proposals, Iris handles client messages, Rick builds deliverables and sets pricing.

## Status: Active (skill #33)

## Setup

### Prerequisites
- Active Upwork freelancer account with completed profile
- Email notifications enabled for: job alerts, messages, offers, contracts, payments
- Upwork email forwarding to Rick's mailbox (or email classification via email-fortress)

### Step 1: Configure RSS Feeds
1. Log into Upwork, search for your target categories
2. On each search results page, copy the RSS feed URL (includes your auth token)
3. Edit `~/rick-vault/upwork/config/rss-feeds.json` — replace placeholder URLs with your authenticated feed URLs
4. Test: `python3 ~/clawd/skills/upwork/scripts/upwork-rss.py` — should find jobs

### Step 2: Customize Scoring
Edit `~/rick-vault/upwork/config/scoring.json`:
- Set `skills_primary` to your main Upwork skills
- Set `skills_secondary` to adjacent skills
- Adjust `budget_ranges` to your target price points
- Add `blacklist_keywords` for jobs to auto-skip

### Step 3: Customize Proposal Templates
Edit `~/rick-vault/upwork/config/templates.json`:
- Update service categories to match your Upwork profile
- Add your own portfolio examples to `value_props`
- Customize `opening_hooks` with your real experience

### Step 4: Set Connect Budget
Edit `~/rick-vault/upwork/config/connects-budget.json`:
- Set `current_balance` to your actual Upwork connects count
- Adjust `weekly_connects_budget` based on your spending plan

### Step 5: Verify
```
/upwork status    — Should show 0 across the board
/upwork connects  — Should show your budget config
```

The heartbeat will automatically start scanning RSS feeds and processing Upwork emails on the next cycle.

## Service Types

| Type | Delivery | Price Range |
|------|----------|-------------|
| AI agent development | Python agents + docs | $200-2000 |
| Python automation | Scripts + tests | $100-500 |
| API integration | Working code + docs | $150-800 |
| Data analysis | Python + output | $100-500 |
| Web scraping | Scraper + data | $100-400 |
| Code review / debugging | Review + fixes | $75-300 |
| Technical writing | Docs / guides | $75-250 |
| Full-stack development | App + deployment | $300-2000 |

## Workflows

### UPWORK_PROPOSAL (priority 25)
5-step pipeline: job analysis -> proposal draft -> pricing -> founder approval -> stage for submission. Email/RSS-triggered or manual via `/upwork bid`.

### UPWORK_CONTRACT (priority 12)
6-step pipeline: intake -> plan -> build -> review -> delivery approval -> deliver. Highest priority — paying client. Includes revision loop and JSS protection.

### UPWORK_MESSAGE (priority 18)
3-step pipeline: classify -> draft response -> stage for sending. Auto-approves in act-then-notify mode.

### UPWORK_POST_PROJECT (priority 30)
2-step pipeline: review request -> follow-up draft. Auto-spawned after contract delivery.

### UPWORK_ANALYTICS (priority 45)
2-step pipeline: win/loss analysis -> strategy adjustment. Weekly cron or manual.

## Telegram Commands

```
/upwork status       — Pipeline summary (proposals/contracts/messages)
/upwork bid <url>    — Queue proposal for specific job
/upwork proposals    — List active proposals
/upwork contracts    — List active contracts with deadlines
/upwork messages     — List pending client messages
/upwork revenue      — Revenue summary (gross, net, connects ROI)
/upwork deliver <id> — Approve + stage delivery
/upwork connects     — Connect balance and budget
/upwork analytics    — Trigger win/loss analysis
```

## Data Ingestion

- **Email**: Upwork emails classified by `upwork-classify.py`, routed via `upwork-monitor.sh`
- **RSS**: Job feeds polled by `upwork-rss.py` every 30 min via heartbeat
- **Submission**: Stage + Telegram notify (founder copies to Upwork UI)

## Approval Policy

- `upwork-proposal-submit`: always requires founder approval (spends connects)
- `upwork-delivery`: always requires founder approval (JSS at stake)
- `upwork-message-response`: act-then-notify, auto-approves in overnight mode

## Data Directory

```
~/rick-vault/upwork/
├── config/           # scoring.json, templates.json, connects-budget.json, rss-feeds.json
├── jobs/             # Classified job JSONs + RSS results + seen-ids.json
├── proposals/        # Per-job: analysis, cover letter, pricing, submit-ready
├── contracts/        # Per-contract: intake, plan, deliverable, review, delivery-package
├── messages/         # Per-thread: classification, response draft
├── post-project/     # Per-contract: review request, follow-up
├── analytics/        # Per-date: win-loss analysis, strategy updates
└── revenue/          # Revenue snapshots
```

## Subagent Routing

- Remy: upwork_job_analysis
- Teagan: upwork_proposal_draft
- Iris: upwork_message_draft, upwork_message_send, upwork_contract_deliver, upwork_review_request, upwork_followup_draft

## Scripts

- `upwork-classify.py` — Email classifier for Upwork notifications
- `upwork-monitor.sh` — Email scan + notification ingestion
- `upwork-rss.py` — RSS job feed scanner
- `upwork-proposal.sh` — Proposal creation CLI helper
- `upwork-contracts.sh` — Contract pipeline status viewer
- `upwork-deliver.sh` — Deliverable staging helper
- `upwork-revenue.py` — Revenue tracking + reporting
