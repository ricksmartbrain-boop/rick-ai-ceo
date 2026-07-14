# Growth Engine Skill

Cross-product growth automation — content repurposing, funnel optimization, A/B test tracking.

## Strategy Framework

### Content Repurposing Pipeline
```
Newsletter Edition
  → LinkedIn post (key insight, 200-300 words)
  → 3 Twitter/X posts (hook + insight)
  → Podcast talking points (5-min segment)
  → Info product chapter seed
```

### Funnel Stages
```
Awareness → Interest → Trial → Paid → Retained → Advocate
```

### Cross-Product Synergies
| From | To | Mechanism |
|------|----|-----------|
| Newsletter | Info Products | CTA in editions → sales page |
| LinkedIn | Partner Connector | Thought leadership → demo requests |
| 404 Agency | Personal Brand | Content experiments → strategy insights |
| Partner Connector | Info Products | War stories → course material |
| Podcast | Newsletter | Episode → written content |

## Commands

### repurpose-content.sh
Transform a newsletter edition into multi-platform content.
```bash
bash scripts/repurpose-content.sh --edition 67   # By edition number
bash scripts/repurpose-content.sh --file PATH     # From file
bash scripts/repurpose-content.sh --topic "topic"  # Generate from scratch
```

### funnel-status.sh
Cross-product funnel metrics.
```bash
bash scripts/funnel-status.sh                    # Full funnel report
bash scripts/funnel-status.sh --product brand    # Personal Brand only
```

## A/B Test Log Format
Tests tracked in ~/rick-vault/decisions/:
```markdown
## A/B Test: [Name]
- **Product:** [product-name]
- **Hypothesis:** [what we think will happen]
- **Variant A:** [control]
- **Variant B:** [test]
- **Metric:** [what we measure]
- **Duration:** [how long]
- **Result:** [winner + data]
- **Revenue Impact:** [$X/month]
```
