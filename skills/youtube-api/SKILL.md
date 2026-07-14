---
name: youtube-api
description: Access YouTube Data API for search, channel insights, and video stats; use yt-dlp for approved downloads.
---

# YouTube API Skill

Use this skill for YouTube discovery, analytics lookups, or media retrieval.

## Prerequisites
- API key stored at `~/.config/youtube/api_key` or exported as `YOUTUBE_API_KEY`
- optional tools:
  - `jq`
  - `yt-dlp`

## Setup
```bash
mkdir -p ~/.config/youtube
printf '%s\n' 'YOUR_YOUTUBE_API_KEY' > ~/.config/youtube/api_key
chmod 600 ~/.config/youtube/api_key
```

## Helper
```bash
YT_KEY="${YOUTUBE_API_KEY:-$(cat ~/.config/youtube/api_key)}"
BASE="https://www.googleapis.com/youtube/v3"
```

## Search Videos
```bash
curl -s "$BASE/search?part=snippet&type=video&maxResults=10&q=autonomous+agents&key=$YT_KEY" | jq
```

## Channel Info
```bash
curl -s "$BASE/channels?part=snippet,statistics&id=<CHANNEL_ID>&key=$YT_KEY" | jq
```

## Video Stats
```bash
curl -s "$BASE/videos?part=snippet,statistics,contentDetails&id=<VIDEO_ID>&key=$YT_KEY" | jq
```

## Download With yt-dlp
Use downloads only when policy and rights allow.

```bash
yt-dlp --skip-download --print "%(_type)s | %(id)s | %(title)s" "https://www.youtube.com/watch?v=<VIDEO_ID>"
```

## Operational Rules
- respect quotas and back off on `429` or `5xx`
- cache IDs and metadata when iterating on analysis
- never hard-code API keys into scripts or commits
