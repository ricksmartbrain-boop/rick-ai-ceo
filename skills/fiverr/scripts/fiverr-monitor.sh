#!/usr/bin/env bash
# fiverr-monitor.sh — Scan email inbox for Fiverr notifications and route to classifier.
# Usage: fiverr-monitor.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
FIVERR_DIR="$DATA_ROOT/fiverr"
MAILBOX_DIR="$DATA_ROOT/mailbox"
CLASSIFIER="$SCRIPT_DIR/fiverr-classify.py"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

mkdir -p "$FIVERR_DIR"/{gigs,orders,inquiries,revenue}

# Scan inbox for unprocessed Fiverr emails
INBOX="$MAILBOX_DIR/inbox"
if [[ ! -d "$INBOX" ]]; then
    echo "No inbox directory at $INBOX"
    exit 0
fi

PROCESSED=0
for email_file in "$INBOX"/fiverr-*.json "$INBOX"/*fiverr*.json; do
    [[ -f "$email_file" ]] || continue

    sender=$(jq -r '.sender // .from // ""' "$email_file" 2>/dev/null)
    subject=$(jq -r '.subject // ""' "$email_file" 2>/dev/null)
    body=$(jq -r '.body // .text // ""' "$email_file" 2>/dev/null)

    if [[ -z "$sender" ]]; then
        continue
    fi

    result=$(python3 "$CLASSIFIER" --sender "$sender" --subject "$subject" --body "$body" --json 2>/dev/null) || continue
    category=$(echo "$result" | jq -r '.category')
    action=$(echo "$result" | jq -r '.action')

    if [[ "$category" == "NOT_FIVERR" ]]; then
        continue
    fi

    echo "[$category] $subject -> $action"

    if [[ "$DRY_RUN" == "true" ]]; then
        ((PROCESSED++))
        continue
    fi

    # Move to processed
    processed_dir="$MAILBOX_DIR/processed/fiverr"
    mkdir -p "$processed_dir"
    mv "$email_file" "$processed_dir/"

    # Route classification result by category
    basename=$(basename "$email_file" .json)
    case "$category" in
        FIVERR_ORDER)
            mkdir -p "$FIVERR_DIR/orders"
            echo "$result" > "$FIVERR_DIR/orders/${basename}-classified.json"
            ;;
        FIVERR_REVIEW)
            mkdir -p "$FIVERR_DIR/revenue/reviews"
            echo "$result" > "$FIVERR_DIR/revenue/reviews/${basename}-classified.json"
            ;;
        *)
            echo "$result" > "$FIVERR_DIR/inquiries/${basename}-classified.json"
            ;;
    esac

    ((PROCESSED++))
done

echo "Processed $PROCESSED Fiverr emails."
