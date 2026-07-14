---
name: research
description: Research topics using Grok's web search and X/Twitter search via the xAI Responses API. Use for finding media appearances, news, people, companies, or any task requiring real-time web data.
---

# Research Skill — Grok Web + X Search

## How It Works
Uses xAI's **Responses API** (`/v1/responses`) with built-in tools (`web_search`, `x_search`) for real-time research. This is NOT the chat completions endpoint — that has no search capability.

## API Key
```bash
# Stored in auth profiles
cat ~/.openclaw/agents/voice/agent/auth-profiles.json | python3 -c "import sys,json; print(json.load(sys.stdin)['profiles']['xai:default']['key'])"
```

## Basic Research Query
```bash
XAI_KEY=$(cat ~/.openclaw/agents/voice/agent/auth-profiles.json | python3 -c "import sys,json; print(json.load(sys.stdin)['profiles']['xai:default']['key'])")

curl -s https://api.x.ai/v1/responses \
  -H "Authorization: Bearer $XAI_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4-1-fast",
    "input": "YOUR RESEARCH QUERY HERE",
    "tools": [{"type": "web_search"}, {"type": "x_search"}]
  }' | python3 -c "
import sys, json
data = json.load(sys.stdin)
for item in data.get('output', []):
    if item.get('type') == 'message':
        for c in item.get('content', []):
            if c.get('type') == 'output_text':
                print(c['text'])
            for ann in c.get('annotations', []):
                if ann.get('url'):
                    print(f'  [{ann[\"url\"]}]')
"
```

## Tool Options
| Tool | Purpose |
|------|---------|
| `web_search` | Search the web, browse pages, extract info |
| `x_search` | Search X/Twitter posts and discussions |

Both can be used together: `"tools": [{"type": "web_search"}, {"type": "x_search"}]`

### Web Search Parameters
```json
{"type": "web_search", "allowed_domains": ["example.com"]}
{"type": "web_search", "excluded_domains": ["reddit.com"]}
```
- `allowed_domains`: Only search within these domains (max 5)
- `excluded_domains`: Exclude these domains (max 5)
- `enable_image_understanding`: Analyze images found during browsing

## Model Requirements
- **Only grok-4 family models** support server-side tools
- Use `grok-4-1-fast` for speed (recommended for most research)
- `grok-3-fast`, `grok-3` etc. do NOT support tools

## Important Notes
- The old `live_search` tool type and `search_parameters` field are **deprecated**
- The `/chat/completions` endpoint does NOT support web search — only `/responses` does
- OpenClaw's `sessions_spawn` can't pass tools through, so always call the API directly via curl
- Responses can take 30-90 seconds for complex queries (Grok does multiple searches + page opens)
- Always use `yieldMs: 90000` and `timeout: 120` for exec calls

## Parsing the Response
The response `output` array contains:
- `web_search_call` items (searches performed, pages opened)
- `x_search_call` items (X searches performed)
- `message` items with `content[].output_text` (the final answer)
- Annotations with citation URLs

## When to Use
- Finding media appearances, podcast episodes, interviews
- Researching people, companies, events
- Checking recent news or social media discussion
- Any task that needs current/real-time web data
- X/Twitter sentiment or discussion analysis

## When NOT to Use
- Simple factual questions (use regular chat)
- Code generation or analysis
- Tasks that don't need web data
