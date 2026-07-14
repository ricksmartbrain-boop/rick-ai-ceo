# Fiverr Skill

Manages Rick's Fiverr seller presence — gig creation, order fulfillment, buyer communication, and revenue tracking.

## Status: Active (skill #32)

## Gig Types

| Type | Delivery | Price Range |
|------|----------|-------------|
| AI agent development | Python scripts + docs | $100-500 |
| Code review/debugging | Written review + fixes | $50-200 |
| Technical writing/docs | Markdown/PDF docs | $50-150 |
| Data analysis scripts | Python + output | $75-250 |
| API integration | Working code + tests | $100-300 |
| Prompt engineering | Prompts + test results | $50-200 |

## Workflows

### FIVERR_GIG_LAUNCH (priority 35)
6-step pipeline to create new gig listings: niche research -> gig copy -> pricing -> portfolio -> founder approval -> publish ready.

### FIVERR_ORDER (priority 15)
6-step pipeline to handle incoming orders: intake -> plan -> build -> review -> delivery approval -> deliver. Higher priority than info products — paying customers waiting.

### FIVERR_INQUIRY (priority 20)
3-step pipeline for buyer messages: classify -> draft response -> stage for sending. Auto-approves in overnight mode.

## Telegram Commands

```
/fiverr status              — Pipeline summary (gigs/orders/inquiries counts)
/fiverr gig <idea>          — Queue gig launch workflow
/fiverr orders              — List active orders with deadlines
/fiverr inquiries           — List pending buyer messages
/fiverr revenue             — Fiverr revenue summary
/fiverr deliver <wf_id>     — Trigger delivery for approved order
```

## Email Integration

Fiverr emails (`@fiverr.com`) are classified by `email-fortress.py` into the FIVERR category, then routed through `fiverr-classify.py` which detects:
- New orders -> auto-queue FIVERR_ORDER workflow
- Buyer messages -> auto-queue FIVERR_INQUIRY workflow
- Reviews -> event logged, Iris drafts response
- Deadline warnings -> ops-alerts notification

## Approval Policy

- `fiverr-gig-publish`: always requires founder approval
- `fiverr-delivery`: always requires founder approval (relaxable after confidence)
- `fiverr-inquiry-response`: act-then-notify, auto-approves in overnight mode

## Data Directory

```
~/rick-vault/fiverr/
├── gigs/{gig-slug}/          # Listing, pricing, portfolio artifacts
├── orders/{order-slug}/      # Intake, plan, deliverable, review, delivery-package
├── inquiries/{id}/           # Classification, response drafts
└── revenue/                  # Revenue snapshots
```

## Subagent Routing

- Remy: fiverr_niche_research
- Teagan: fiverr_gig_copy, fiverr_gig_portfolio
- Iris: fiverr_inquiry_draft, fiverr_inquiry_send, fiverr_order_deliver

## Scripts

- `fiverr-classify.py` — Email classifier for Fiverr notifications
- `fiverr-monitor.sh` — Email scan + notification ingestion
- `fiverr-gig.sh` — Gig creation CLI helper
- `fiverr-orders.sh` — Order pipeline status viewer
- `fiverr-deliver.sh` — Deliverable staging helper
- `fiverr-revenue.py` — Revenue tracking + reporting
