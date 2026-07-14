#!/usr/bin/env bash
# upwork-proposal.sh — Queue an Upwork proposal workflow.
# Usage: upwork-proposal.sh <job-title-or-url> [--category <category>]
set -euo pipefail

DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"

CATEGORY=""
INPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --category) CATEGORY="$2"; shift 2 ;;
        *) INPUT="$INPUT $1"; shift ;;
    esac
done

INPUT="${INPUT## }"

if [[ -z "$INPUT" ]]; then
    echo "Usage: upwork-proposal.sh <job-title-or-url> [--category <category>]"
    echo "Categories: ai-agent, python-automation, api-integration, data-analysis, scraping, code-review, technical-writing, fullstack"
    exit 1
fi

SLUG=$(echo "$INPUT" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | sed 's/--*/-/g' | sed 's/^-\|-$//g' | cut -c1-80)

# Validate slug to prevent path traversal
if [[ -z "$SLUG" ]] || [[ ! "$SLUG" =~ ^[a-z0-9][a-z0-9-]*[a-z0-9]$ ]]; then
    echo "Invalid slug generated from input: $SLUG"
    exit 1
fi

PROPOSAL_DIR="$DATA_ROOT/upwork/proposals/$SLUG"
mkdir -p "$PROPOSAL_DIR"

jq -n \
  --arg input "$INPUT" \
  --arg category "$CATEGORY" \
  --arg slug "$SLUG" \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%S)" \
  '{input: $input, category: $category, slug: $slug, created_at: $created_at, status: "queued"}' \
  > "$PROPOSAL_DIR/proposal-brief.json"

echo "Proposal brief created at $PROPOSAL_DIR/proposal-brief.json"
echo "Slug: $SLUG"
echo "Queue the workflow via Telegram: /upwork bid $INPUT"
