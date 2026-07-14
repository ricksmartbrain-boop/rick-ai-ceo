#!/usr/bin/env bash
# Transform newsletter content into multi-platform posts.
#
# Usage:
#   repurpose-content.sh --edition N     # Repurpose newsletter edition N
#   repurpose-content.sh --file PATH     # Repurpose content from file
#   repurpose-content.sh --topic "topic" # Generate content framework for topic

set -euo pipefail

EDITION=""
FILE=""
TOPIC=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --edition) EDITION="$2"; shift 2 ;;
        --file) FILE="$2"; shift 2 ;;
        --topic) TOPIC="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "═══════════════════════════════════════"
echo "  Growth Engine — Content Repurposer"
echo "  $(date '+%Y-%m-%d %H:%M')"
echo "═══════════════════════════════════════"
echo ""

# Load content
CONTENT=""
SOURCE=""

if [ -n "$FILE" ]; then
    if [ -f "$FILE" ]; then
        CONTENT=$(cat "$FILE")
        SOURCE="File: $FILE"
    else
        echo "❌ File not found: $FILE"
        exit 1
    fi
elif [ -n "$EDITION" ]; then
    VAULT="${RICK_SHARED_VAULT_ROOT:-$HOME/rick-vault}"
    EDITION_FILE=$(find "$VAULT" -name "*Edition*$EDITION*" -o -name "*edition*$EDITION*" 2>/dev/null | head -1)

    if [ -n "$EDITION_FILE" ] && [ -f "$EDITION_FILE" ]; then
        CONTENT=$(cat "$EDITION_FILE")
        SOURCE="Newsletter Edition #$EDITION: $EDITION_FILE"
    else
        echo "⚠️  Newsletter edition #$EDITION not found in vault"
        echo "  Searched: $VAULT"
        echo ""
        echo "💡 Provide content directly:"
        echo "  repurpose-content.sh --file /path/to/content.md"
        exit 1
    fi
elif [ -n "$TOPIC" ]; then
    SOURCE="Topic: $TOPIC"
    echo "📝 Content Framework for: $TOPIC"
    echo ""
    echo "## LinkedIn Post (200-300 words)"
    echo "---"
    echo "Hook: [Contrarian take or surprising insight about $TOPIC]"
    echo ""
    echo "Body: Share a specific story or data point. What did you learn?"
    echo ""
    echo "CTA: What should the reader do next? (Comment, subscribe, try something)"
    echo ""
    echo ""
    echo "## Twitter/X Thread (3-5 tweets)"
    echo "---"
    echo "Tweet 1 (Hook): [Bold statement about $TOPIC that stops scrolling]"
    echo "Tweet 2 (Context): [Why this matters / the problem]"
    echo "Tweet 3 (Insight): [Your key learning or data]"
    echo "Tweet 4 (Action): [What to do about it]"
    echo "Tweet 5 (CTA): [Newsletter link or ask for engagement]"
    echo ""
    echo ""
    echo "## Podcast Talking Points (5-min segment)"
    echo "---"
    echo "1. Opening hook: Why $TOPIC matters right now"
    echo "2. Personal story or case study"
    echo "3. Key insight or framework"
    echo "4. Actionable takeaway for listeners"
    echo "5. Tease next episode or newsletter"
    echo ""
    echo ""
    echo "## Info Product Seed"
    echo "---"
    echo "Chapter title: [Based on $TOPIC]"
    echo "Key concepts to expand: [List 3-5]"
    echo "Exercises/worksheets: [List 1-2]"
    exit 0
fi

if [ -n "$CONTENT" ]; then
    echo "📄 Source: $SOURCE"
    echo ""
    WORD_COUNT=$(echo "$CONTENT" | wc -w | tr -d ' ')
    echo "📊 Content: $WORD_COUNT words"
    echo ""
    echo "═══════════════════════════════════════"
    echo ""
    echo "## Repurposing Opportunities"
    echo ""
    echo "### 1. LinkedIn Post"
    echo "Extract the key insight or contrarian take from the newsletter."
    echo "Target: 200-300 words with a personal angle."
    echo ""
    echo "### 2. Twitter/X Thread"
    echo "Break into 3-5 tweet thread. Lead with the most surprising point."
    echo ""
    echo "### 3. Podcast Segment"
    echo "Use as a 5-minute talking point. Add personal stories."
    echo ""
    echo "### 4. Info Product Seed"
    echo "Tag key concepts that could become course modules."
    echo ""
    echo "💡 Use Claude to draft each format from the source content."
    echo "   Pass the content + format template to get drafts."
fi
