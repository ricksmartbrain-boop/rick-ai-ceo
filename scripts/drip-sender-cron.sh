#!/bin/bash
# Wrapper for drip-sender.py that loads the Resend API key
# Called by cron hourly
set -euo pipefail

# Load env
source /Users/rickthebot/.openclaw/workspace/config/rick.env 2>/dev/null || true

# Run the drip sender (uses email-course-drip.py which reads drip-state.json + Resend audience)
# NOTE: drip-sender.py is the old stub (reads email-drip/subscribers.json, 1 done entry) — do not use
exec /usr/bin/python3 /Users/rickthebot/rick-vault/projects/email-course-ai-ceo/scripts/email-course-drip.py
