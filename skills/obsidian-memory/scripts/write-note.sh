#!/usr/bin/env bash
# Write a note to Rick's vault with proper frontmatter.
#
# Usage:
#   write-note.sh --type TYPE --path PATH --title TITLE [--content CONTENT] [--tags TAG1,TAG2]
#   echo "content" | write-note.sh --type TYPE --path PATH --title TITLE --stdin
#
# Examples:
#   write-note.sh --type revenue --path "revenue/2026-03-05.md" --title "Revenue Snapshot" --content "MRR: $5000"
#   write-note.sh --type decision --path "decisions/2026-03-05-pricing.md" --title "Pricing Change" --content "Increased from $99 to $149"

set -euo pipefail

RICK_DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
VAULT="$RICK_DATA_ROOT"
TYPE=""
FPATH=""
TITLE=""
CONTENT=""
TAGS=""
USE_STDIN=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --type) TYPE="$2"; shift 2 ;;
        --path) FPATH="$2"; shift 2 ;;
        --title) TITLE="$2"; shift 2 ;;
        --content) CONTENT="$2"; shift 2 ;;
        --tags) TAGS="$2"; shift 2 ;;
        --stdin) USE_STDIN=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$TYPE" ] || [ -z "$FPATH" ] || [ -z "$TITLE" ]; then
    echo "Usage: write-note.sh --type TYPE --path PATH --title TITLE [--content CONTENT] [--tags TAG1,TAG2]"
    exit 1
fi

FULL_PATH="$VAULT/$FPATH"
DIR=$(dirname "$FULL_PATH")
mkdir -p "$DIR"

# Read content from stdin if requested
if $USE_STDIN; then
    CONTENT=$(cat)
fi

# Build frontmatter
DATE=$(date +%Y-%m-%d)
FRONTMATTER="---
type: $TYPE
title: \"$TITLE\"
created: $DATE"

if [ -n "$TAGS" ]; then
    FRONTMATTER="$FRONTMATTER
tags: [$TAGS]"
fi

FRONTMATTER="$FRONTMATTER
---"

# Write file
cat > "$FULL_PATH" << EOF
$FRONTMATTER

# $TITLE

$CONTENT
EOF

echo "Written: $FULL_PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/rebuild-memory-index.py" rebuild --write --quiet >/dev/null 2>&1 || true
