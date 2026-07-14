# SociaVault — Social Media Data API

Pull public social media data from 25+ platforms via REST API. Use for lead enrichment, buyer intent mining, competitor intelligence, and social proof harvesting.

## Auth

- API key: `SOCIAVAULT_API_KEY` in `~/clawd/config/rick.env`
- Base URL: `https://api.sociavault.com/v1/scrape`
- Header: `X-API-Key: YOUR_KEY`
- Credits: 1 credit per call (most endpoints). Check balance at `/v1/credits`.

## Core Endpoints (mapped to Rick's workflows)

### Lead Enrichment (before outreach)
```bash
# X/Twitter profile
curl -s "https://api.sociavault.com/v1/scrape/twitter/profile?handle=TARGET" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# LinkedIn profile
curl -s "https://api.sociavault.com/v1/scrape/linkedin/profile?url=LINKEDIN_URL" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# Instagram profile
curl -s "https://api.sociavault.com/v1/scrape/instagram/profile?handle=TARGET" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"
```

### Buyer Intent Mining (find prospects)
```bash
# Reddit search for buyer signals
curl -s "https://api.sociavault.com/v1/scrape/reddit/search?query=QUERY&limit=10" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# Twitter search
curl -s "https://api.sociavault.com/v1/scrape/twitter/search?query=QUERY&limit=10" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# Threads search
curl -s "https://api.sociavault.com/v1/scrape/threads/search?query=QUERY&limit=10" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"
```

### Competitor Ad Intelligence
```bash
# Facebook Ad Library — search by keyword
curl -s "https://api.sociavault.com/v1/scrape/facebook-ad-library/search?query=AI+agent&limit=10" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# Google Ad Library — find company ads
curl -s "https://api.sociavault.com/v1/scrape/google-ad-library/search-advertisers?query=COMPANY" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# LinkedIn Ad Library
curl -s "https://api.sociavault.com/v1/scrape/linkedin-ad-library/search?query=QUERY" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"
```

### Trend Monitoring
```bash
# TikTok profile/videos
curl -s "https://api.sociavault.com/v1/scrape/tiktok/profile?handle=TARGET" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"

# YouTube channel
curl -s "https://api.sociavault.com/v1/scrape/youtube/channel?handle=TARGET" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"
```

### Social Proof Harvesting
```bash
# Google search for mentions
curl -s "https://api.sociavault.com/v1/scrape/google/search?query=meetrick.ai+OR+MeetRickAI" \
  -H "X-API-Key: $SOCIAVAULT_API_KEY"
```

## All Supported Platforms

TikTok, Instagram, YouTube, Facebook, Twitter/X, Reddit, LinkedIn, Threads, Pinterest, TikTok Shop, Facebook Ad Library, Google Ad Library, LinkedIn Ad Library, Google Search.

## Helper Script

`scripts/sociavault.sh` — thin wrapper for common operations.

Usage:
```bash
bash scripts/sociavault.sh enrich-x <handle>
bash scripts/sociavault.sh enrich-linkedin <url>
bash scripts/sociavault.sh buyer-intent <query>
bash scripts/sociavault.sh competitor-ads <company>
bash scripts/sociavault.sh credits
```

## Budget Rules

- 5,530 credits at setup (2026-04-09)
- 1 credit per call (most endpoints)
- Do NOT bulk-scrape. Use strategically for enrichment + intent signals.
- Check credits before any batch operation (>20 calls).
- Alert if credits drop below 500.
