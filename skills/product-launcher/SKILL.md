# Product Launcher Skill

Autonomous product creation and launch pipeline -- from idea validation to post-launch optimization.

## Triggers

- New product idea validated via audience feedback
- Revenue target requires new product (current products plateauing)
- Nightly review identifies market opportunity
- Newsletter engagement signals demand for a topic

## Product Launch Playbook (Felix-Proven, Rick-Adapted)

### Phase 0: Validation (1-2 days)
1. Research market demand (Google Trends, competitor analysis, audience polls)
2. Check newsletter engagement on related topics (open rates, click rates)
3. Validate with social posts -- gauge interest before building
4. Go/no-go decision: **founder approval required for >$500 investment**

### Phase 1: Build (3-7 days)
1. Generate outline using `info-products` skill (`create-outline.sh`)
2. Write content via Ralph coding loops (iterative drafts)
3. Design and format (PDF, video, or interactive)
4. Create Stripe product + pricing via `create-product.sh`
5. Quality review -- proofread, test all links, verify payment flow

### Phase 2: Pre-Launch (2-3 days)
1. Build landing page via `website-builder` skill
2. Set up checkout flow (Stripe payment links or embedded checkout)
3. Draft launch newsletter edition (hook, value prop, CTA)
4. Prepare social posts (LinkedIn, X/Twitter, cross-platform)
5. Set up email welcome sequence (3-email drip for buyers)

### Phase 3: Launch (1 day)
1. Deploy landing page to production
2. Send launch newsletter to full list
3. Post launch content across all social channels
4. Monitor real-time sales and conversion rates
5. Respond to early buyer questions and feedback

### Phase 4: Post-Launch (7 days)
1. Daily sales tracking (revenue, units, conversion rate)
2. Collect customer feedback (survey or direct outreach)
3. Gather social proof (testimonials, screenshots, quotes)
4. A/B test landing page (headline, CTA, pricing)
5. Write case study for future content marketing

## Product Types & Revenue Targets

| Type | Time to Build | Price Range | Target Monthly |
|------|---------------|-------------|----------------|
| PDF Guide | 2-3 days | $19-$49 | $2K-$5K |
| Mini Course | 5-7 days | $49-$99 | $5K-$10K |
| Full Course | 14-21 days | $99-$299 | $10K-$30K |
| Template Pack | 1-2 days | $9-$29 | $1K-$3K |
| SaaS Tool | 30+ days | $9-$49/mo | $5K-$20K |

## Build vs. Buy Decision Framework

| Investment | Action | Approval |
|------------|--------|----------|
| Under $500 | Skip -- too small to move the needle | Auto |
| $500-$5K | Quick guide or template pack | Auto |
| $5K-$20K | Full course with marketing push | Auto |
| $20K+ | SaaS tool -- significant commitment | founder approval required |

## Commands

### product-launch.sh
Manage the full product launch lifecycle.
```bash
bash scripts/product-launch.sh --plan <product>        # Generate launch plan
bash scripts/product-launch.sh --build <product>       # Kick off build phase
bash scripts/product-launch.sh --status <product>      # Check build/launch status
bash scripts/product-launch.sh --launch <product>      # Execute launch sequence
bash scripts/product-launch.sh --post-launch <product> # Monitor first 48h
```

### create-product.sh
Create a new product with Stripe integration and scaffolding.
```bash
bash scripts/create-product.sh --type guide --name "AI Agent Playbook" --price 29
bash scripts/create-product.sh --type course --name "Building AI Agents" --price 99
bash scripts/create-product.sh --type template --name "SaaS Launch Kit" --price 19
bash scripts/create-product.sh --type tool --name "Revenue Tracker" --price 29
```

## Cross-Skill Dependencies

| Skill | Usage |
|-------|-------|
| `info-products` | Outline generation, content pipeline |
| `website-builder` | Landing page creation |
| `newsletter` | Launch email, nurture sequences |
| `social-manager` | Launch announcements, social proof |
| `growth-engine` | Content repurposing, funnel optimization |
| `metrics` | Sales tracking, conversion analytics |

## Revenue Target: $100,000/month (across all products)
- 3-5 evergreen courses at $99-$299
- 5-10 guides/templates at $9-$49
- 1-2 SaaS tools at $9-$49/month
- Bundle pricing for multi-product purchases
