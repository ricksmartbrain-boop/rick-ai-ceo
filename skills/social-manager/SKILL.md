# Social Manager Skill

Manage Rick-owned social surfaces — post content, track metrics, and repurpose across platforms.

## Platform APIs

### LinkedIn
- **API:** LinkedIn REST API v2
- **Auth:** OAuth 2.0 via `LINKEDIN_ACCESS_TOKEN` env var
- **Key endpoints:**
  - `POST /ugcPosts` — create a post
  - `GET /organizationalEntityShareStatistics` — post analytics
  - `GET /connections` — network stats

### Instagram
- **API:** Instagram Graph API via Meta Business Suite
- **Auth:** `INSTAGRAM_ACCESS_TOKEN` env var
- **Business Account ID:** `INSTAGRAM_BUSINESS_ID` env var
- **Key endpoints:**
  - `POST /{ig-user-id}/media` — create media container
  - `POST /{ig-user-id}/media_publish` — publish container
  - `GET /{ig-user-id}/insights` — account metrics

### TikTok (Future)
- **API:** TikTok Content Posting API
- **Auth:** `TIKTOK_ACCESS_TOKEN` env var
- **Status:** Not yet active. Accounts to be created for 404 Agency models.

## Posting Cadence

| Platform | Frequency | Account |
|----------|-----------|---------|
| LinkedIn | 3-5x/week | Rick / founder profile |
| Instagram | 3x/week | 404 Agency (Cat & Luna) |
| Instagram | 2x/week | Rick brand |
| X/Twitter | Daily | Handled by `x-api` skill |
| TikTok | Future | 404 Agency models |

## Cross-Platform Strategy

Content flows from long-form to short-form, never identical across platforms:

```
Newsletter edition
  -> LinkedIn article (condensed, professional tone)
    -> Twitter thread (key takeaways, punchy)
      -> Instagram carousel (visual, 5-7 slides)
```

- LinkedIn: Professional, thought leadership, longer captions
- Instagram: Visual-first, carousel storytelling, hashtags
- Twitter: Conversational, threads, engagement hooks (handled by x-api skill)
- TikTok: Short video, trending audio (future)

## Commands

### social-post.sh
Post content to LinkedIn or Instagram.

```bash
# Post to LinkedIn
social-post.sh --platform linkedin --text "AI agents are the new SaaS..."

# Post to Instagram with image
social-post.sh --platform instagram --text "Behind the scenes" --image photo.jpg

# Schedule for later
social-post.sh --platform linkedin --text "Thread coming..." --schedule "2026-03-10T14:00:00Z"

# List recent posts
social-post.sh --list linkedin
```

### social-metrics.sh
Track social media performance.

```bash
# LinkedIn metrics for this week
social-metrics.sh --platform linkedin --period week

# All platforms monthly overview
social-metrics.sh --platform all --period month
```

### social-repurpose.sh
Repurpose content across platforms.

```bash
# Turn a newsletter into platform-specific posts
social-repurpose.sh --source newsletter --input edition-68.md

# Repurpose a tweet thread
social-repurpose.sh --source tweet --input thread.md
```
