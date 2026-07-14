# Info Products Skill

Digital product engine — course/guide creation pipeline, launch management, sales tracking.

## Product Types
| Type | Price Range | Effort | Examples |
|------|-------------|--------|---------|
| Guide (PDF) | $9–$29 | Low | Checklists, templates, playbooks |
| Mini-Course | $29–$99 | Medium | 5-10 video lessons + worksheets |
| Full Course | $99–$299 | High | 20+ lessons, community access |
| Community | $29–$99/mo | Ongoing | Slack/Discord, weekly calls |

## Pricing Strategy
```
Free (lead magnet) → $29 (guide) → $99 (course) → $299 (premium) → $99/mo (community)
```

## Launch Playbook
1. **Pre-launch (4 weeks):** Build waitlist via newsletter CTA
2. **Beta (2 weeks):** 20 beta users at 50% discount for feedback
3. **Launch (1 week):** Full price + bonuses, 3-email sequence
4. **Post-launch:** Evergreen funnel, testimonials, iterate

## Commands

### product-pipeline.sh
View products by stage.
```bash
bash scripts/product-pipeline.sh            # All products
bash scripts/product-pipeline.sh --stage draft  # Only drafts
```

### create-outline.sh
Generate course outline from topic.
```bash
bash scripts/create-outline.sh --topic "Building AI Agents"
bash scripts/create-outline.sh --topic "B2B Lead Generation" --type mini-course
```

## Revenue Target: $40,000/month
- 2-3 evergreen courses at $99-$299
- Bundle pricing for 2+ courses
- Community membership at $99/month
- Affiliate revenue from partner promotions

## Content Sources
- 67 newsletter editions (Beehiiv)
- 9 podcast episodes ("Not Me")
- Partner Connector development experience (14 sprints)
- AI agent building expertise
- B2B sales and lead gen knowledge
