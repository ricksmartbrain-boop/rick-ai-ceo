#!/usr/bin/env python3
"""Lead replay — wake up dormant leads by queuing deal_close workflows.

Reads every email-addressable lead from ~/rick-vault/projects/outreach/*.jsonl,
filters against suppression + already-touched-in-last-30-days, and queues a
deal_close workflow for N of them per run. Default 22/run × 2 runs/day =
44 leads/day, so a ~40-lead pool clears in ~1 day and the cycle repeats
after the 30-day touch cooldown.

Schedule via launchd at 9am + 1pm M-F (see ai.rick.lead-replay.plist).

Safety:
- Dry-run default — emits nothing unless --live is passed.
- Suppression list honored: ~/rick-vault/mailbox/suppression.txt.
- Touch-cooldown: if a deal_close workflow already exists for this email
  with status in (queued, active, done) in the last 30 days, skip.
- Per-run emit cap (default 22) prevents flooding the queue.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Bootstrap path so `from runtime import ...` works regardless of CWD.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect as db_connect  # noqa: E402
from runtime.engine import queue_deal_close_workflow  # noqa: E402


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OUTREACH_DIR = DATA_ROOT / "projects" / "outreach"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
LOG_FILE = DATA_ROOT / "operations" / "lead-replay.jsonl"
TOUCH_COOLDOWN_DAYS = 30


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_suppressions() -> set[str]:
    if not SUPPRESSION_FILE.exists():
        return set()
    out: set[str] = set()
    for line in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if trimmed and not trimmed.startswith("#"):
            out.add(trimmed.lower())
    return out


def load_leads() -> list[dict]:
    """Read every .jsonl lead file except backup/invalid and return rows with email."""
    if not OUTREACH_DIR.exists():
        return []
    leads: list[dict] = []
    for f in sorted(OUTREACH_DIR.glob("*.jsonl")):
        if any(tag in f.name for tag in ("backup", "invalid")):
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            email = (row.get("email") or "").strip().lower()
            if not email or "@" not in email:
                continue
            leads.append(
                {
                    "email": email,
                    "name": row.get("name") or "",
                    "context": row.get("context") or row.get("product") or row.get("website") or "",
                    "source_file": f.name,
                    "raw": row,
                }
            )
    return leads


def recently_touched_emails(conn, days: int = TOUCH_COOLDOWN_DAYS) -> set[str]:
    """Emails with a deal_close workflow created in the last `days` days.
    The trigger_payload is stored in workflows.context_json, so we JSON-scan
    the string for the email instead of building an index."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT context_json
          FROM workflows
         WHERE kind = 'deal_close'
           AND created_at >= ?
        """,
        (cutoff,),
    ).fetchall()
    touched: set[str] = set()
    for row in rows:
        try:
            ctx = json.loads(row["context_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        email = ((ctx.get("trigger_payload") or {}).get("email") or "").strip().lower()
        if email:
            touched.add(email)
    return touched


def log_event(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit", type=int, default=22, help="Max leads to queue this run")
    parser.add_argument("--dry-run", action="store_true", default=True, help="List only (default)")
    parser.add_argument("--live", dest="dry_run", action="store_false", help="Actually queue workflows")
    parser.add_argument("--seed", type=int, default=None, help="Shuffle seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    suppressions = load_suppressions()
    leads = load_leads()
    print(f"[lead-replay] loaded {len(leads)} email-addressable leads across outreach files")

    conn = db_connect()
    try:
        touched = recently_touched_emails(conn)
        print(f"[lead-replay] {len(touched)} recently-touched emails to skip")

        # Filter + dedupe by email
        seen: set[str] = set()
        fresh: list[dict] = []
        for lead in leads:
            e = lead["email"]
            if e in seen or e in touched or e in suppressions:
                continue
            seen.add(e)
            fresh.append(lead)
        print(f"[lead-replay] {len(fresh)} leads eligible after suppression + cooldown + dedupe")

        random.shuffle(fresh)
        batch = fresh[: int(args.emit)]
        print(f"[lead-replay] emitting {len(batch)} this run (dry_run={args.dry_run})")

        queued = []
        for lead in batch:
            entry = {
                "ran_at": now_iso(),
                "email": lead["email"],
                "name": lead["name"],
                "source_file": lead["source_file"],
                "dry_run": args.dry_run,
            }
            if args.dry_run:
                entry["action"] = "would-queue-deal_close"
                print(f"  [dry] {lead['email']:40s}  name={lead['name'][:30]}  src={lead['source_file']}")
            else:
                wf_id = queue_deal_close_workflow(
                    conn,
                    email=lead["email"],
                    name=lead["name"],
                    source="lead-replay",
                    message=lead["context"][:500],
                )
                entry["action"] = "queued"
                entry["workflow_id"] = wf_id
                print(f"  [live] queued {wf_id} for {lead['email']}")
            queued.append(entry)
            log_event(entry)

        summary = {
            "ran_at": now_iso(),
            "total_leads": len(leads),
            "eligible_after_filters": len(fresh),
            "emitted": len(queued),
            "dry_run": args.dry_run,
        }
        print(json.dumps(summary, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
