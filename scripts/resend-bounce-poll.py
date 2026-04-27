#!/usr/bin/env python3
"""resend-bounce-poll.py — Poll Resend API for bounce/complaint events every 5 min.

Fetches recent emails from Resend, detects hard-bounces and complaints, appends to:
  ~/rick-vault/operations/email-bounces.jsonl

Hard-bounces and complaints are auto-suppressed in:
  ~/rick-vault/mailbox/suppression.txt

Writes a poll.done sentinel row after every run so flag_health.py can detect
liveness (probe key: RICK_BOUNCE_POLL_LIVE, threshold 1.0h).

Two-phase scan:
  Phase 1: Resend list API top-100 emails (sorted newest first, page param is no-op).
  Phase 2: Direct GET /emails/{id} for any IDs in email-sends.jsonl not yet resolved.
           Catches bounces on emails that scroll off the list API window.

API endpoints used:
  GET https://api.resend.com/emails?limit=100         (list)
  GET https://api.resend.com/emails/{id}              (direct lookup)

Runs via LaunchAgent ai.rick.resend-bounce-poll.plist (StartInterval=300).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
BOUNCES_FILE = DATA_ROOT / "operations" / "email-bounces.jsonl"
SENDS_FILE = DATA_ROOT / "operations" / "email-sends.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
# IDs already confirmed delivered/opened/clicked — skip re-checking these.
BOUNCE_STATE_FILE = DATA_ROOT / "control" / "email-bounce-check-state.json"

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_BASE = "https://api.resend.com"

BOUNCE_EVENTS = {"bounced", "complained"}
AUTO_SUPPRESS_EVENTS = {"bounced", "complained"}  # both kill sender rep

# Emails logged in email-sends.jsonl within this window get a direct ID check.
SEND_CHECK_WINDOW_DAYS = 7


# ── helpers ───────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        # Resend blocks Python's default urllib User-Agent with 403.
        "User-Agent": "rick-bounce-poll/1.0",
    }


def fetch_email_list() -> list[dict]:
    """Return the 100 most-recently-sent emails from Resend.

    The list API page param is a no-op (returns same top-100 regardless).
    We do a single fetch and supplement with direct ID lookups for sent emails
    we have tracked in email-sends.jsonl.
    """
    req = Request(f"{RESEND_BASE}/emails?limit=100", headers=_headers(), method="GET")
    resp = urlopen(req, timeout=20)
    return json.loads(resp.read()).get("data", [])


def fetch_email_by_id(email_id: str) -> dict | None:
    req = Request(f"{RESEND_BASE}/emails/{email_id}", headers=_headers(), method="GET")
    try:
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read())
    except (HTTPError, URLError):
        return None


def load_known_bounce_ids() -> set[str]:
    """Load email IDs already logged in email-bounces.jsonl."""
    known: set[str] = set()
    if not BOUNCES_FILE.exists():
        return known
    try:
        for line in BOUNCES_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
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


def load_resolved_ids() -> set[str]:
    """IDs confirmed as delivered/opened/clicked — no point re-checking."""
    if not BOUNCE_STATE_FILE.exists():
        return set()
    try:
        state = json.loads(BOUNCE_STATE_FILE.read_text(encoding="utf-8"))
        return set(state.get("resolved", []))
    except (OSError, json.JSONDecodeError):
        return set()


def save_resolved_ids(resolved: set[str]) -> None:
    try:
        BOUNCE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        ids = sorted(resolved)[-5000:]  # cap to bound file size
        BOUNCE_STATE_FILE.write_text(
            json.dumps({"resolved": ids}, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def load_sends_ids() -> list[tuple[str, str]]:
    """Return (message_id, ts) pairs from email-sends.jsonl within the check window."""
    if not SENDS_FILE.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEND_CHECK_WINDOW_DAYS)).isoformat()
    results: list[tuple[str, str]] = []
    try:
        for line in SENDS_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                mid = row.get("message_id", "")
                ts = row.get("ts", "")
                if mid and ts >= cutoff[:19]:
                    results.append((mid, ts))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return results


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


def process_email(
    email: dict,
    known_ids: set,
    suppressions: set,
    resolved_ids: set,
    counters: dict,
) -> None:
    """Log a bounce/complaint row and update state sets in-place."""
    event = email.get("last_event")
    email_id = email.get("id")
    if not email_id:
        return

    if event not in BOUNCE_EVENTS:
        # Confirm as resolved so we skip direct re-checks next poll.
        if event in ("delivered", "opened", "clicked"):
            resolved_ids.add(email_id)
        return

    if email_id in known_ids:
        return  # already logged

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
        counters["bounces"] += 1
        print(f"  ✗ bounce: {recipient} (id={email_id[:8]})")
    elif event == "complained":
        counters["complaints"] += 1
        print(f"  ⚠ complaint: {recipient} (id={email_id[:8]})")

    if event in AUTO_SUPPRESS_EVENTS:
        email_lower = recipient.lower()
        if email_lower not in suppressions:
            reason = "hard_bounce" if event == "bounced" else "spam_complaint"
            append_suppression(recipient, reason)
            suppressions.add(email_lower)
            counters["suppressed"] += 1


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not RESEND_API_KEY:
        print("ERROR: RESEND_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    known_ids = load_known_bounce_ids()
    suppressions = load_suppressions()
    resolved_ids = load_resolved_ids()
    counters = {"bounces": 0, "complaints": 0, "suppressed": 0, "errors": 0}

    # ── Phase 1: list API — top 100 most-recent emails ────────────────────────
    try:
        for email in fetch_email_list():
            process_email(email, known_ids, suppressions, resolved_ids, counters)
    except HTTPError as e:
        print(f"  ERROR list API HTTP {e.code}: {e.reason}", file=sys.stderr)
        counters["errors"] += 1
    except (URLError, Exception) as e:
        print(f"  ERROR list API: {e}", file=sys.stderr)
        counters["errors"] += 1

    # ── Phase 2: direct ID checks for emails in email-sends.jsonl ────────────
    # These may scroll off the list API top-100 window if large batches go out.
    sends = load_sends_ids()
    direct_checked = 0
    for mid, _ts in sends:
        if mid in known_ids or mid in resolved_ids:
            continue
        email = fetch_email_by_id(mid)
        if email is None:
            counters["errors"] += 1
            continue
        process_email(email, known_ids, suppressions, resolved_ids, counters)
        direct_checked += 1

    save_resolved_ids(resolved_ids)

    # ── Sentinel: always written — liveness signal for flag_health.py ─────────
    sentinel = {
        "ts": now_iso(),
        "event": "poll.done",
        "new_bounces": counters["bounces"],
        "new_complaints": counters["complaints"],
        "new_suppressed": counters["suppressed"],
        "direct_checked": direct_checked,
        "errors": counters["errors"],
    }
    append_bounce_row(sentinel)

    print(
        f"✓ resend-bounce-poll: {counters['bounces']} bounces, "
        f"{counters['complaints']} complaints, {counters['suppressed']} auto-suppressed, "
        f"{direct_checked} direct ID checks, {counters['errors']} errors"
    )


if __name__ == "__main__":
    main()
