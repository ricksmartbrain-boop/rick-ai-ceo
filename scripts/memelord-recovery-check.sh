#!/bin/bash
code=$(curl -s --max-time 10 -o /dev/null -w '%{http_code}' -X POST 'https://www.memelord.com/api/v1/ai-meme' -H "Authorization: Bearer $MEMELORD_API_KEY" -H 'Content-Type: application/json' -d '{"prompt":"test","count":1}')
if [ "$code" = "200" ]; then
  python3 /Users/rickthebot/.openclaw/workspace/runtime/runner.py telegram --text 'Memelord API is back! Running pipeline now.' --chat-id 203132131
  python3 /Users/rickthebot/.openclaw/workspace/scripts/memelord-pipeline.py
fi
