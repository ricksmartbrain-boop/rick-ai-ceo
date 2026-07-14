---
name: x-api
description: Post tweets, read mentions, reply, like, retweet, and search on X/Twitter using the official v2 API. Use for all X interactions instead of bird-cli or browser automation.
---

# X API Skill — Rick

Default X/Twitter interactions go through `xpost`. If it is not on PATH, use `RICK_XPOST_BIN`.

For MCP-native access, OpenClaw has two configured servers:
- `x-docs`: remote docs MCP at `https://docs.x.com/mcp`; probes cleanly and exposes docs search/read tools.
- `xapi`: stdio bridge using `npx -y @xdevplatform/xurl mcp https://api.x.com/mcp`; static config is valid, but user-context API calls require OAuth2 auth in `xurl`.

As of 2026-06-30, Homebrew `xurl` cask is `1.0.3` and does not expose `mcp`; the npm launcher resolves `@xdevplatform/xurl` `1.2.2` and does expose `mcp`. Prefer the npm launcher for MCP until the cask catches up.

## Setup
API keys stored at `~/.config/x-api/keys.env`. Format:
```
X_API_KEY=...
X_API_SECRET=...
X_ACCESS_TOKEN=...
X_ACCESS_TOKEN_SECRET=...
X_USER_ID=...
```

`~/.xurl` has a `meetrick` app with OAuth1 and bearer credentials, but no OAuth2 user token yet. The X MCP full route needs OAuth2 for user-context tools and writes. Run a headless OAuth2 login when ready:

```bash
npx -y @xdevplatform/xurl auth oauth2 --app meetrick --headless
```

If first-run browser auth is acceptable, probing/using `xapi` may open the browser and block until login completes. X requires the app redirect URI `http://localhost:8080/callback` unless `REDIRECT_URI` is set and registered.

## Commands

### Post a tweet
```bash
xpost post "Your tweet text here"
```

### Reply to a tweet
```bash
xpost reply <tweet_id> "Your reply text"
```

### Quote tweet
```bash
xpost quote <tweet_id> "Your quote text"
```

### Get mentions (last N)
```bash
xpost mentions [--count 20]
```

### Get user timeline
```bash
xpost timeline <username> [--count 10]
```

### Search recent tweets
```bash
xpost search "query string" [--count 10]
```

### Like a tweet
```bash
xpost like <tweet_id>
```

### Retweet
```bash
xpost retweet <tweet_id>
```

### Delete a tweet
```bash
xpost delete <tweet_id>
```

### Get a single tweet
```bash
xpost get <tweet_id>
```

### Get home timeline (reverse chronological)
```bash
xpost home [--count 20]
```

## Output
All commands output JSON by default. Use `--pretty` for formatted output or `--text` for plain text summary.

## Rate Limits (Basic Tier — $200/mo)
- POST tweets: 100/15min, 10,000/24hrs
- GET mentions: 300/15min per user
- GET timeline: 900/15min per user
- GET home: 180/15min per user
- Search recent: 300/15min per user
- Likes: 50/15min, 1,000/24hrs

## Engagement Rules
- **Reply to relevant mentions of Rick's handle** — always
- **Proactive replies only to AI agents** — no unsolicited replies to humans
- Tweet content: AI products, entrepreneurship, lead generation, B2B growth, builder experiments. No customer support tweets.
- Tone: Match SOUL.md — sharp, warm, conversational, not corporate
