#!/usr/bin/env bash
# roast-site.sh — Roast a website URL and output structured analysis
# Usage: bash roast-site.sh <url> [output_format: json|thread]
set -euo pipefail

URL="${1:-}"
FORMAT="${2:-json}"

if [[ -z "$URL" ]]; then
  echo "Usage: $0 <url> [json|thread]"
  exit 1
fi

source ~/clawd/config/rick.env 2>/dev/null || true

# Fetch the page content
PAGE_CONTENT=$(curl -sL --max-time 15 "$URL" | head -c 50000 | sed 's/<script[^>]*>.*<\/script>//g' | sed 's/<style[^>]*>.*<\/style>//g' | sed 's/<[^>]*>//g' | tr -s '[:space:]' ' ' | head -c 8000)

if [[ -z "$PAGE_CONTENT" ]]; then
  echo '{"error": "Could not fetch page content"}'
  exit 1
fi

# Use OpenAI to roast
PROMPT="You are Rick, an AI CEO who does brutally honest but constructive website roasts. Analyze this website and provide:

1. SCORE (1-10)
2. TOP 3 PROBLEMS (specific, actionable)
3. TOP 3 WINS (what's working)
4. VERDICT (one punchy line)
5. ESTIMATED REVENUE IMPACT (what fixing the problems could mean)

URL: $URL

Page content (extracted text):
$PAGE_CONTENT

Be specific. Reference actual elements on the page. No generic advice. Be entertaining but genuinely helpful."

if [[ "$FORMAT" == "thread" ]]; then
  PROMPT="$PROMPT

Format as a Twitter thread (5-6 tweets, each under 280 chars). Start with '🔥 SITE ROAST: [domain]' and end with 'Want yours roasted free? meetrick.ai/roast'"
fi

RESPONSE=$(curl -s https://api.openai.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d "$(jq -n --arg prompt "$PROMPT" '{
    model: "gpt-4o-mini",
    messages: [{role: "system", content: "You are Rick, AI CEO of meetrick.ai. Brutally honest, constructive, entertaining."}, {role: "user", content: $prompt}],
    temperature: 0.8,
    max_tokens: 1500
  }')")

echo "$RESPONSE" | jq -r '.choices[0].message.content'
