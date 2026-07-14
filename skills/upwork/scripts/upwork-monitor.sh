#!/usr/bin/env bash
# upwork-monitor.sh — Scan email inbox for Upwork notifications and route to classifier.
# Usage: upwork-monitor.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_ROOT="${RICK_DATA_ROOT:-$HOME/rick-vault}"
UPWORK_DIR="$DATA_ROOT/upwork"
MAILBOX_DIR="$DATA_ROOT/mailbox"
CLASSIFIER="$SCRIPT_DIR/upwork-classify.py"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

mkdir -p "$UPWORK_DIR"/{jobs,proposals,contracts,messages,revenue}

# Scan inbox for unprocessed Upwork emails
INBOX="$MAILBOX_DIR/inbox"
if [[ ! -d "$INBOX" ]]; then
    echo "No inbox directory at $INBOX"
    exit 0
fi

PROCESSED=0
for email_file in "$INBOX"/upwork-*.json "$INBOX"/*upwork*.json; do
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

    if [[ "$category" == "NOT_UPWORK" ]]; then
        continue
    fi

    echo "[$category] $subject -> $action"

    if [[ "$DRY_RUN" == "true" ]]; then
        ((PROCESSED++))
        continue
    fi

    # Move to processed
    processed_dir="$MAILBOX_DIR/processed/upwork"
    mkdir -p "$processed_dir"
    mv "$email_file" "$processed_dir/"

    # Route classification result by category
    basename=$(basename "$email_file" .json)
    case "$category" in
        UPWORK_JOB_MATCH|UPWORK_INVITATION)
            echo "$result" > "$UPWORK_DIR/jobs/${basename}-classified.json"
            ;;
        UPWORK_OFFER|UPWORK_CONTRACT)
            echo "$result" > "$UPWORK_DIR/contracts/${basename}-classified.json"
            ;;
        UPWORK_PAYMENT|UPWORK_REVIEW)
            mkdir -p "$UPWORK_DIR/revenue"
            echo "$result" > "$UPWORK_DIR/revenue/${basename}-classified.json"
            ;;
        *)
            echo "$result" > "$UPWORK_DIR/messages/${basename}-classified.json"
            ;;
    esac

    ((PROCESSED++))
done

echo "Processed $PROCESSED Upwork emails."
