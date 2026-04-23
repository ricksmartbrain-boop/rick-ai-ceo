#!/usr/bin/env python3
"""Wave-6 TIER-A #3 — inject a synthetic warm lead for end-to-end pipeline test.

Drops a hand-crafted triage row into ~/rick-vault/mailbox/triage/test-injected/
that's separate from real inbound (so production reply-router doesn't pick it
up). Then runs reply_router.process_file directly on just that file. Verifies:

  1. classifier output (you set it via --kind)
  2. dispatcher fires (counter-pitch draft, alert-Vlad, deal_close queue)
  3. drafts land in correct directory

Usage:
  python3 ~/clawd/scripts/inject-test-warm-lead.py --kind objection_with_counter
  python3 ~/clawd/scripts/inject-test-warm-lead.py --kind sales_inquiry --phone "+15551234567"
  python3 ~/clawd/scripts/inject-test-warm-lead.py --kind pricing_question --keep

By default the injected file is deleted after dispatch (so it doesn't leak
into mailbox-digest counts). Use --keep to leave it on disk for inspection.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
TEST_TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage" / "test-injected"

PRESETS = {
    "sales_inquiry": {
        "from": "alex.test@example-prospect.com",
        "from_name": "Alex Test",
        "subject": "Saw the meetrick.ai roast — interested in Pro",
        "body": ("Hey Rick, I just ran our domain through the roast tool and the "
                 "feedback was sharp. Want to upgrade to Pro this week. Can you walk "
                 "me through what onboarding looks like for a 3-person team?"),
    },
    "objection_with_counter": {
        "from": "sarah.test@founderlytics.example",
        "from_name": "Sarah Test",
        "subject": "Re: Your demo + question on pricing",
        "body": ("Hey Rick, thanks for the demo. I am interested but $29/mo feels "
                 "high vs running my own scripts. Plus I am skeptical that an "
                 "autonomous agent can really replace my manual flow. Got case studies?"),
    },
    "pricing_question": {
        "from": "morgan.test@example-startup.io",
        "from_name": "Morgan Test",
        "subject": "Quick pricing question",
        "body": ("Hi — what's the difference between Pro $29 and Managed $499? "
                 "We're a 5-person company doing $200K ARR, leaning toward Pro but "
                 "want to know what triggers the upgrade."),
    },
    "scheduling_request": {
        "from": "jordan.test@example-agency.com",
        "from_name": "Jordan Test",
        "subject": "15 min this week?",
        "body": ("Saw your daily diary — would love 15 min to walk through how "
                 "we could use Rick for our 12-person agency. Free Thu/Fri 2-4 PT?"),
    },
    "support_request": {
        "from": "newton.test@example-customer.com",
        "from_name": "Newton Test",
        "subject": "Rick stopped posting on Bluesky after upgrade",
        "body": ("Hey — I'm on Pro $29 since last week. Diary used to cross-post "
                 "to Bluesky but stopped 3 days ago. Logs show 'auth fail'. "
                 "Help?"),
    },
    "question": {
        "from": "casey.test@example.com",
        "from_name": "Casey Test",
        "subject": "How does Rick handle MCP servers?",
        "body": ("Just curious — does Rick install MCP servers automatically when "
                 "I tell him to handle a new domain (e.g. analytics)? Or do I have "
                 "to wire each one manually?"),
    },
}


def inject(kind: str, phone: str | None, keep: bool, dry_run: bool) -> dict:
    if kind not in PRESETS:
        return {"status": "error", "error": f"unknown kind '{kind}'. Choices: {list(PRESETS)}"}

    base = PRESETS[kind]
    thread_id = f"test-injected-{kind}-{uuid.uuid4().hex[:8]}"
    sig = {"name": base["from_name"], "title": "Founder", "company": "Example Co"}
    if phone:
        sig["phone"] = phone

    row = {
        "id": thread_id,
        "message_id": f"<{thread_id}@inject.test>",
        "in_reply_to": "",
        "references": [],
        "thread_id": thread_id,
        "from": base["from"],
        "from_name": base["from_name"],
        "subject": base["subject"],
        "body": base["body"],
        "has_html": False,
        "received_at": datetime.now().isoformat(timespec="seconds"),
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
        "signature": sig,
        "classification": kind,
        "classified_at": datetime.now().isoformat(timespec="seconds"),
    }

    TEST_TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = TEST_TRIAGE_DIR / f"injected-{thread_id}.jsonl"
    path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")

    if dry_run:
        return {"status": "dry-run", "path": str(path), "row": row}

    from runtime.reply_router import process_file, db_connect  # noqa: WPS433
    conn = db_connect()
    try:
        result = process_file(conn, path, dry_run=False, batch_cap=1)
    finally:
        conn.close()

    if not keep:
        try:
            path.unlink()
        except OSError:
            pass

    return {"status": "dispatched", "result": result, "thread_id": thread_id,
            "kept_file": keep}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", required=True, choices=sorted(PRESETS.keys()))
    ap.add_argument("--phone", default=None,
                    help="Add E.164 phone to signature (triggers SMS draft path)")
    ap.add_argument("--keep", action="store_true",
                    help="Leave injected file on disk for inspection")
    ap.add_argument("--dry-run", action="store_true",
                    help="Just write the file, don't dispatch through router")
    args = ap.parse_args()

    result = inject(args.kind, args.phone, args.keep, args.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in ("dry-run", "dispatched") else 1


if __name__ == "__main__":
    sys.exit(main())
