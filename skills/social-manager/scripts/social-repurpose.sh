#!/usr/bin/env bash
set -euo pipefail

# social-repurpose.sh — Repurpose content across social media platforms.

usage() {
  cat <<EOF
Usage: social-repurpose.sh [OPTIONS]

Repurpose content from one format into platform-specific versions.

Options:
  --source <newsletter|tweet|blog>   Source content type (required)
  --input <file>                     Source content file (required)
  -h, --help                         Show this help

Output:
  Prints platform-specific adapted versions to stdout, separated by headers.

Examples:
  social-repurpose.sh --source newsletter --input ~/rick-vault/content/newsletters/drafts/edition-68.md
  social-repurpose.sh --source tweet --input thread.md
  social-repurpose.sh --source blog --input article.md
EOF
  exit 0
}

SOURCE=""
INPUT=""

if [[ $# -eq 0 ]]; then
  usage
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE="$2"
      shift 2
      ;;
    --input)
      INPUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$SOURCE" ]]; then
  echo "Error: --source is required" >&2
  exit 1
fi

if [[ -z "$INPUT" ]]; then
  echo "Error: --input is required" >&2
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Error: Input file not found: $INPUT" >&2
  exit 1
fi

# Validate source type
case "$SOURCE" in
  newsletter|tweet|blog) ;;
  *)
    echo "Error: --source must be one of: newsletter, tweet, blog" >&2
    exit 1
    ;;
esac

CONTENT=$(cat "$INPUT")
RICK_PUBLIC_AUTHOR="${RICK_PUBLIC_AUTHOR:-Rick}"
RICK_BRAND_POSITIONING="${RICK_BRAND_POSITIONING:-AI founder and operator building autonomous revenue systems in public.}"

PROMPT="You are a social media content strategist for ${RICK_PUBLIC_AUTHOR}.

Brand positioning: ${RICK_BRAND_POSITIONING}

Given the following ${SOURCE} content, create adapted versions for each platform. Never copy content verbatim across platforms — adapt the tone, length, and format for each.

SOURCE CONTENT:
---
${CONTENT}
---

Create the following versions:

## LINKEDIN POST
- Professional, thought-leadership tone
- 1,300 characters max (LinkedIn sweet spot)
- Start with a hook (question or bold statement)
- Include 2-3 line breaks for readability
- End with a question to drive engagement
- No hashtags (they reduce reach on LinkedIn)

## INSTAGRAM CAPTION
- Conversational, relatable tone
- Start with an attention-grabbing first line (shows in preview)
- Use line breaks and short paragraphs
- Include a clear CTA (save, share, comment)
- Add 5-10 relevant hashtags at the end
- 2,200 characters max

## TWITTER THREAD
- Punchy, conversational tone
- First tweet is the hook (must stand alone)
- 5-8 tweets, each under 280 characters
- Number each tweet (1/, 2/, etc.)
- End with a summary tweet and CTA
- Include a 'Follow for more' at the end

## INSTAGRAM CAROUSEL OUTLINE
- 5-7 slides
- Slide 1: Hook title (big, bold text)
- Slides 2-6: One key point per slide with supporting detail
- Final slide: CTA (follow, save, share)
- Write the text for each slide"

# Generate via Claude CLI
if command -v claude &>/dev/null; then
  claude --print "$PROMPT"
elif command -v anthropic &>/dev/null; then
  anthropic messages create \
    --model claude-sonnet-4-20250514 \
    --max-tokens 4096 \
    -m "user:$PROMPT" \
    --no-stream \
    | jq -r '.content[0].text'
else
  echo "Error: No Claude CLI found. Install 'claude' or 'anthropic' CLI." >&2
  echo "" >&2
  echo "Prompt saved below for manual use:" >&2
  echo "===================================" >&2
  echo "$PROMPT"
  exit 1
fi
