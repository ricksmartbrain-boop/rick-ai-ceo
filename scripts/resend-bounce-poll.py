#!/usr/bin/env python3
"""resend-bounce-poll.py — Poll Resend API for bounce/complaint events every 5 min.

Fetches recent emails from Resend, detects hard-bounces and complaints, appends to:
  ~/rick-vault/operations/email-bounces.jsonl

Hard-bounces and complaints are auto-suppressed in:
  ~/rick-vault/mailbox/suppression.txt

Writes a poll.done sentinel row after every run so flag_health.py can detect
liveness (probe key: RICK_BOUNCE_POLL_LIVE, threshold 1.0h).

API: GET https://api.resend.com/emails?limit=100&page=N
Filter: last_event in {bounced, complained}
Dedup: email IDs already present in email-bounces.jsonl are skipped.

Runs via LaunchAgent ai.rick.resend-bounce-poll.plist (StartInterval=300).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
BOUNCES_FILE = DATA_ROOT / "operations" / "email-bounces.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_BASE = "https://api.resend.com"

BOUNCE_EVENTS = {"bounced", "complained"}
# Both hard-bounces AND complaints get auto-suppressed.
# Spam complaints are fatal to sender reputation — never retry.
AUTO_SUPPRESS_EVENTS = {"bounced", "complained"}

MAX_PAGES = 5      # 500 emails — covers ~3 weeks at 15–22 sends/day
PAGE_LIMIT = 100


# ── helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_known_bounce_ids() -> set[str]:
    """Load email IDs already logged in email-bounces.jsonl to avoid duplicates."""
    known: set[str] = set()
    if not BOUNCES_FILE.exists():
        return known
    try:
        for line in BOUNCES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                eid = row.get("email_id")
                if eid:
                    known.add(eid)
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return known


def load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    try:
        return {
            line.split()[0].lower()
            for line in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    except OSError:
        return set()


def append_suppression(email: str, reason: str) -> None:
    try:
        SUPPRESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SUPPRESSION_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{email}  # {reason} {now_iso()}\n")
        print(f"  → suppressed: {email} ({reason})")
    except OSError as e:
        print(f"  WARN: suppression write failed: {e}", file=sys.stderr)


def append_bounce_row(row: dict) -> None:
    try:
        BOUNCES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with BOUNCES_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as e:
        print(f"  WARN: bounce log write failed: {e}", file=sys.stderr)


def fetch_email_page(page: int) -> dict:
    url = f"{RESEND_BASE}/emails?limit={PAGE_LIMIT}&page={page}"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            # Resend blocks Python's default urllib User-Agent (403).
            "User-Agent": "rick-bounce-poll/1.0",
        },
        method="GET",
    )
    resp = urlopen(req, timeout=20)
    return json.loads(resp.read())


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not RESEND_API_KEY:
        print("ERROR: RESEND_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    known_ids = load_known_bounce_ids()
    suppressions = load_suppressions()

    new_bounces = 0
    new_complaints = 0
    new_suppressed = 0
    pages_fetched = 0
    errors = 0

    for page in range(1, MAX_PAGES + 1):
        try:
            data = fetch_email_page(page)
            pages_fetched += 1
        except HTTPError as e:
            print(f"  ERROR HTTP {e.code} fetching page {page}: {e.reason}", file=sys.stderr)
            errors += 1
            break
        except (URLError, Exception) as e:
            print(f"  ERROR fetching page {page}: {e}", file=sys.stderr)
            errors += 1
            break

        emails = data.get("data", [])
        if not emails:
            break

        for email in emails:
            event = email.get("last_event")
            if event not in BOUNCE_EVENTS:
                continue

            email_id = email.get("id")
            if not email_id or email_id in known_ids:
                continue  # already logged

            to_list = email.get("to", [])
            recipient = to_list[0] if to_list else "unknown"

            row = {
                "ts": now_iso(),
                "email_id": email_id,
                "event": event,
                "to": recipient,
                "from": email.get("from", ""),
                "subject": email.get("subject", ""),
                "sent_at": email.get("created_at", ""),
            }
            append_bounce_row(row)
            known_ids.add(email_id)

            if event == "bounced":
                new_bounces += 1
                print(f"  ✗ bounce: {recipient} (id={email_id})")
            elif event == "complained":
                new_complaints += 1
                print(f"  ⚠ complaint: {recipient} (id={email_id})")

            # Auto-suppress both bounces and spam complaints
            if event in AUTO_SUPPRESS_EVENTS:
                email_lower = recipient.lower()
                if email_lower not in suppressions:
                    reason = "hard_bounce" if event == "bounced" else "spam_complaint"
                    append_suppression(recipient, reason)
                    suppressions.add(email_lower)
                    new_suppressed += 1

        if not data.get("has_more", False):
            break

    # ── Sentinel row: always written, used by flag_health.py probe ────────────
    sentinel = {
        "ts": now_iso(),
        "event": "poll.done",
        "pages_fetched": pages_fetched,
        "new_bounces": new_bounces,
        "new_complaints": new_complaints,
        "new_suppressed": new_suppressed,
        "errors": errors,
    }
    append_bounce_row(sentinel)

    print(
        f"✓ resend-bounce-poll: {new_bounces} bounces, {new_complaints} complaints, "
        f"{new_suppressed} auto-suppressed ({pages_fetched} pages, {errors} errors)"
    )


if __name__ == "__main__":
    main()
