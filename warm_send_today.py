#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import requests

from runtime.email_validator import validate_for_outbound
from nurture_emails import email_1

AUDIENCE = Path(os.path.expanduser("~/rick-vault/audiences/warm-general-validated.jsonl"))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "Rick <rick@meetrick.ai>"
REPLY_TO = "rick@meetrick.ai"
RESEND_URL = "https://api.resend.com/emails"
TODAY = date.today().isoformat()
LIMIT = 3


def load_rows():
    rows = []
    with AUDIENCE.open() as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def save_rows(rows):
    tmp = AUDIENCE.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    tmp.replace(AUDIENCE)


def main():
    if not RESEND_API_KEY:
        raise SystemExit("RESEND_API_KEY missing")

    rows = load_rows()
    pending = [r for r in rows if r.get("stage") == "warm_reengage_pending"]
    batch = pending[:LIMIT]
    if not batch:
        print("No pending warm contacts found.")
        return 0

    sent = []
    for row in batch:
        email = row["email"]
        ok, reason = validate_for_outbound(email)
        if not ok:
            print(f"SKIP {email} :: {reason}")
            continue
        subject, html = email_1(row.get("first_name") or "there", row.get("url") or "your site")
        payload = {
            "from": FROM_EMAIL,
            "to": [email],
            "reply_to": REPLY_TO,
            "subject": subject,
            "html": html,
        }
        resp = requests.post(
            RESEND_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        row["stage"] = f"warm_reengage_sent_{TODAY}"
        row["last_sent_at"] = TODAY
        sent.append(email)
        print(f"SENT {email} :: {resp.json().get('id', '?')}")

    save_rows(rows)
    print(f"batch_sent={len(sent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
