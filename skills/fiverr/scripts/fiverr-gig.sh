#!/usr/bin/env bash
# fiverr-gig.sh — Queue a Fiverr gig launch workflow.
# Usage: fiverr-gig.sh <gig-idea> [--type <type>]
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "$0")/../../../" && pwd)"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

GIG_TYPE="ai-agent-development"
IDEA=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --type) GIG_TYPE="$2"; shift 2 ;;
        *) IDEA="$IDEA $1"; shift ;;
    esac
done

IDEA="${IDEA## }"

if [[ -z "$IDEA" ]]; then
    echo "Usage: fiverr-gig.sh <gig-idea> [--type <type>]"
    echo "Types: ai-agent-development, code-review, technical-writing, data-analysis, api-integration, prompt-engineering"
    exit 1
fi

SLUG=$(echo "$IDEA" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-\|-$//g')

GIG_DIR="$DATA_ROOT/fiverr/gigs/$SLUG"
mkdir -p "$GIG_DIR"

jq -n \
  --arg idea "$IDEA" \
  --arg type "$GIG_TYPE" \
  --arg slug "$SLUG" \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%S)" \
  '{idea: $idea, type: $type, slug: $slug, created_at: $created_at, status: "queued"}' \
  > "$GIG_DIR/gig-brief.json"

echo "Gig brief created at $GIG_DIR/gig-brief.json"
echo "Slug: $SLUG"
echo "Queue the workflow via Telegram: /fiverr gig $IDEA"
