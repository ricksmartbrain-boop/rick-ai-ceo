#!/usr/bin/env bash
# Generate a blog hero image via Google Gemini.
# Usage: bash generate.sh "description of image" [output-path]
#
# API key: ~/.config/gemini/api_key or GEMINI_API_KEY env var
# Cost: Variable (Gemini pricing)

set -euo pipefail

PROMPT_DESC="${1:?Usage: generate.sh \"image description\" [output.png]}"
OUTPUT="${2:-hero-image.png}"

# Load API key
if [[ -f ~/.config/gemini/api_key ]]; then
    GEMINI_API_KEY=$(cat ~/.config/gemini/api_key)
elif [[ -z "${GEMINI_API_KEY:-}" ]]; then
    echo "Error: Gemini API key not found" >&2
    echo "  Set at ~/.config/gemini/api_key or GEMINI_API_KEY env var" >&2
    exit 1
fi

FULL_PROMPT="Generate a blog hero image: bright, modern, clean composition showing ${PROMPT_DESC}. No text in the image. Photorealistic editorial style, warm natural lighting."

echo "Generating image: ${PROMPT_DESC}"

curl -s -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent?key=${GEMINI_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json
print(json.dumps({
    'contents': [{'parts': [{'text': '''${FULL_PROMPT}'''}]}],
    'generationConfig': {'responseModalities': ['TEXT', 'IMAGE']}
}))
")" | python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
for part in data.get('candidates', [{}])[0].get('content', {}).get('parts', []):
    if 'inlineData' in part:
        img = base64.b64decode(part['inlineData']['data'])
        with open('${OUTPUT}', 'wb') as f:
            f.write(img)
        print(f'Saved ${OUTPUT} ({len(img)} bytes)')
        sys.exit(0)
print('Error: No image in response', file=sys.stderr)
sys.exit(1)
"
