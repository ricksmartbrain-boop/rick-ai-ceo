#!/usr/bin/env python3
"""
Deterministic escalation fire-guard. Replaces the fragile shell `grep $(date ...)` step
that the agent harness kept mangling.

Logic:
  - Read ~/rick-vault/logs/escalation.log
  - If today's date already has a FIRED marker -> print ALREADY_FIRED, exit 0
  - Else append a dated FIRED marker -> print TRIGGER_WARROOM, exit 0

The caller (cron) only runs this when revenue-escalation-check.sh returned exit code 1.
"""
import os
import datetime

LOG = os.path.expanduser("~/rick-vault/logs/escalation.log")
today = datetime.date.today().isoformat()

os.makedirs(os.path.dirname(LOG), exist_ok=True)

existing = ""
if os.path.exists(LOG):
    with open(LOG, "r", encoding="utf-8") as fh:
        existing = fh.read()

if today in existing and "FIRED" in existing:
    # Confirm today specifically has a FIRED line
    for line in existing.splitlines():
        if today in line and "FIRED" in line:
            print("ALREADY_FIRED")
            raise SystemExit(0)

stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
with open(LOG, "a", encoding="utf-8") as fh:
    fh.write(f"{stamp} ESCALATION FIRED ({today})\n")
print("TRIGGER_WARROOM")
