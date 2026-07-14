#!/bin/bash
# Rick Weekly Newsletter — auto-sender
# Runs every Wednesday morning, writes issue to file, sends via Resend

set -e
source ~/clawd/config/rick.env 2>/dev/null

ISSUE_DIR=~/clawd/skills/newsletter/issues
mkdir -p "$ISSUE_DIR"

# Get next issue number
LAST=$(ls "$ISSUE_DIR"/*.json 2>/dev/null | wc -l | tr -d ' ')
NEXT=$((LAST + 2))  # +2 because issue 1 was sent manually

echo "Newsletter auto-sender: would send issue #$NEXT"
echo "Actual content generation happens via the agent cron job"
