# Brand Builder Skill

Personal brand strategy and growth engine -- positioning, content pillars, audience growth, and channel management.

## Brand Positioning

**"AI-first operator building $100K/month with autonomous systems in public."**

Rick's public brand should feel like a real operating log of AI-powered entrepreneurship: launches, revenue moves, failures, fixes, and compounding lessons shared transparently.

## Content Pillars

| Pillar | Focus | Content Examples |
|--------|-------|-----------------|
| **AI & Automation** | Building AI agents, automation tools, LLM workflows | Agent architecture, prompt engineering, tool comparisons |
| **Entrepreneurship** | Revenue building, product launches, business strategy | Revenue reports, launch case studies, pricing experiments |
| **Building in Public** | Transparent journey, wins and failures, real numbers | Monthly reports, behind-the-scenes, tool stack reveals |
| **Deep Dives** | Technical walkthroughs, detailed breakdowns | Code walkthroughs, system design, integration guides |

## Voice & Tone

- **Direct** -- No fluff, get to the point. Respect the reader's time.
- **Data-driven** -- Back claims with numbers. Revenue, metrics, percentages.
- **Vulnerable about failures** -- Share what didn't work. Losses build credibility.
- **Excited about tech** -- Genuine enthusiasm for AI and automation.
- **No guru energy** -- Never "I'll teach you my secrets." Instead: "Here's what I tried."

## Growth Metrics (Tracked Weekly)

| Channel | Current | Target (Q2 2026) | Growth Rate |
|---------|---------|-------------------|-------------|
| Newsletter subscribers | [check Beehiiv] | 5,000 | +200/week |
| LinkedIn followers | [check LinkedIn] | 10,000 | +300/week |
| X/Twitter followers | [check X] | 5,000 | +150/week |
| Total audience reach | [sum] | 20,000 | +650/week |

## Growth Levers

### 1. Content Consistency
- Newsletter: 1x/week (Sunday)
- LinkedIn: 3-5x/week
- X/Twitter: 1-2x/day
- Podcast: 2x/month

### 2. Cross-Platform Repurposing
Every newsletter becomes: LinkedIn post + X thread + podcast segment (via growth-engine skill).

### 3. Engagement Strategy
- Reply to every comment in first hour
- Engage with 10 relevant posts daily
- DM top engagers monthly
- Feature community wins in content

### 4. Guest Appearances
- Podcast guesting: 2x/month target
- LinkedIn Lives or Twitter Spaces: 1x/month
- Conference speaking: 1x/quarter

### 5. Viral Content Formats
- "I built X in Y hours" threads
- Revenue transparency posts
- AI tool comparison posts
- "Unpopular opinion" takes
- Behind-the-scenes process reveals

## Commands

### brand-audit.sh
Audit brand presence across all channels.
```bash
bash scripts/brand-audit.sh --full                # Full audit (all channels)
bash scripts/brand-audit.sh --channel newsletter  # Newsletter only
bash scripts/brand-audit.sh --channel linkedin     # LinkedIn only
bash scripts/brand-audit.sh --channel twitter      # X/Twitter only
bash scripts/brand-audit.sh --channel instagram    # Instagram only
```

### brand-content.sh
Generate content drafts for any platform.
```bash
bash scripts/brand-content.sh --type thread --pillar ai --hook "Built an AI CEO in 48 hours"
bash scripts/brand-content.sh --type article --pillar entrepreneurship --hook "First $10K month"
bash scripts/brand-content.sh --type post --pillar building --hook "My agent stack"
bash scripts/brand-content.sh --type story --pillar lessons --hook "The launch that flopped"
```

## Brand Assets

| Asset | Location |
|-------|----------|
| Bio (short) | "AI-first entrepreneur. Building $100K/month with autonomous agents. Newsletter: [link]" |
| Bio (long) | [see brand-audit.sh output] |
| Headshot | [TBD] |
| Brand colors | [TBD] |
| Logo | [TBD] |

## Cross-Skill Dependencies

| Skill | Usage |
|-------|-------|
| `newsletter` | Primary content channel, subscriber growth |
| `social-manager` | Multi-platform posting, engagement tracking |
| `growth-engine` | Content repurposing, funnel optimization |
| `metrics` | Audience analytics, growth tracking |
| `product-launcher` | Product launches drive brand authority |

## Revenue from Brand: $20,000/month target
- Sponsorships: $5K-$10K/month (newsletter + LinkedIn)
- Affiliate revenue: $2K-$5K/month (tool recommendations)
- Speaking fees: $3K-$5K/quarter
- Brand drives all product sales (indirect revenue)
