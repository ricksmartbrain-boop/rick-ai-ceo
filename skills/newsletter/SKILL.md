# Newsletter Skill

Manage Rick's newsletter — write drafts, publish to Beehiiv, and track performance.

## Platform APIs

### Beehiiv
- **API:** `https://api.beehiiv.com/v2/`
- **Auth:** Bearer token via `BEEHIIV_API_KEY` env var
- **Publication ID:** `BEEHIIV_PUB_ID` env var
- **Key endpoints:**
  - `GET /publications/{pub_id}/posts` — list posts
  - `POST /publications/{pub_id}/posts` — create/schedule post
  - `GET /publications/{pub_id}/subscriptions` — subscriber data
  - `GET /publications/{pub_id}/posts/{post_id}` — post stats (opens, clicks)

### Substack
- No official API. Draft locally in markdown, then post manually or migrate to Beehiiv.

## Content Calendar

- **Cadence:** Weekly edition every Tuesday, 9 AM EST
- **Topic rotation:**
  1. AI insights (industry trends, new models, practical applications)
  2. Building in public (behind-the-scenes, metrics, lessons)
  3. Product launches (new products, updates, case studies)
  4. Industry analysis (market moves, competitor landscape, predictions)

## Content Rules

- Always include exactly 1 CTA per edition — either newsletter growth OR product promotion, never both
- **Newsletter-to-product funnel:**
  - Free newsletter builds trust and authority
  - Every 4th edition includes a soft product CTA (mention, not hard sell)
  - Launch editions are dedicated product announcements (scheduled separately from rotation)

## Commands

### newsletter-send.sh
Publish, schedule, and track newsletter editions via Beehiiv API.

```bash
# Send a draft to Beehiiv
newsletter-send.sh --platform beehiiv --draft ~/rick-vault/content/newsletters/drafts/edition-68.md

# Schedule for later
newsletter-send.sh --platform beehiiv --draft draft.md --schedule "2026-03-10T09:00:00-05:00"

# List recent editions with stats
newsletter-send.sh --list

# Subscriber and performance stats
newsletter-send.sh --stats

# Export subscriber count
newsletter-send.sh --subscribers
```

### newsletter-write.sh
Generate newsletter drafts using Claude.

```bash
# Write a draft on a topic
newsletter-write.sh --topic "Why AI agents will replace SaaS dashboards"

# Include a product CTA
newsletter-write.sh --topic "Building Rick in public" --product "Rick"

# Set tone and length
newsletter-write.sh --topic "Q1 2026 AI landscape" --tone insight --length long
```
