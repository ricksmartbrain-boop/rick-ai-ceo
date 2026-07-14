#!/usr/bin/env bash
set -euo pipefail

# Generate a Claw Mart marketplace manifest for a skill.
#
# Usage:
#   generate-manifest.sh --skill sentry-autofix --price 29 --tier paid
#   generate-manifest.sh --skill obsidian-memory --price 0 --tier free

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SKILL=""
PRICE="0"
TIER="free"
TAGS=""
DESCRIPTION=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --skill) SKILL="$2"; shift 2 ;;
        --price) PRICE="$2"; shift 2 ;;
        --tier) TIER="$2"; shift 2 ;;
        --tags) TAGS="$2"; shift 2 ;;
        --description) DESCRIPTION="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$SKILL" ]]; then
    echo "Usage: generate-manifest.sh --skill <name> --price <usd> --tier <free|paid>"
    exit 1
fi

SKILL_DIR="$ROOT_DIR/$SKILL"
if [[ ! -f "$SKILL_DIR/SKILL.md" ]]; then
    echo "Error: SKILL.md not found in $SKILL_DIR"
    exit 1
fi

# Auto-extract description from SKILL.md first line after title
if [[ -z "$DESCRIPTION" ]]; then
    DESCRIPTION=$(grep -m1 '^[^#]' "$SKILL_DIR/SKILL.md" | head -c 200)
fi

# Auto-detect required env vars
REQUIRES="[]"
if [[ -d "$SKILL_DIR/scripts" ]]; then
    REQUIRES=$(grep -rh '\${\|os.getenv\|os.environ' "$SKILL_DIR/scripts/" 2>/dev/null \
        | grep -oE '[A-Z_]{4,}' \
        | sort -u \
        | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))" 2>/dev/null || echo "[]")
fi

# Count files
FILE_COUNT=$(find "$SKILL_DIR" -type f | wc -l | tr -d ' ')

# Build tags array
if [[ -n "$TAGS" ]]; then
    TAGS_JSON=$(echo "$TAGS" | tr ',' '\n' | python3 -c "import sys,json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
else
    TAGS_JSON="[\"$SKILL\"]"
fi

cat <<EOF
{
  "name": "$SKILL",
  "description": "$DESCRIPTION",
  "author": "rick",
  "price_usd": $PRICE,
  "tier": "$TIER",
  "tags": $TAGS_JSON,
  "file_count": $FILE_COUNT,
  "requires": $REQUIRES,
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
