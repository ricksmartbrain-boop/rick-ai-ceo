#!/usr/bin/env python3
"""TIER-0 #1 follow-up — cost_attribution writer.

The cost_attribution table was migrated 2026-04-23 (commit 093a5dd) to link
every outcome → eventual workflow terminal status, but no INSERT site was
added. This standalone sweeper closes the loop: scans recent terminal
workflows and back-fills cost_attribution rows.

Architecture choice: standalone cron (not engine.py edit) because:
1. Zero risk to live job-processing flow
2. Can backfill historical workflows without daemon restart
3. Easy to disable (just unload the plist)

Runs daily 02:00 local. argparse:
  --dry-run       no writes (default)
  --since-days N  only attribute workflows updated in last N days (default 30)
  --backfill-all  attribute every terminal workflow (slow, one-time use)

Output: idempotent INSERTs (UNIQUE(outcome_id) constraint prevents dupes).
Logs to ~/rick-vault/operations/attribute-costs.jsonl.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "attribute-costs.jsonl"

TERMINAL_STATUSES = {
    "done": "succeeded",
    "published": "succeeded",
    "fulfilled": "succeeded",
    "launch-ready": "succeeded",
    "failed": "failed",
    "escalated": "failed",
    "cancelled": "abandoned",
    "denied": "abandoned",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = _now_iso()
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def attribute(con: sqlite3.Connection, since_days: int, backfill_all: bool, live: bool) -> dict:
    """For every terminal workflow in window, INSERT cost_attribution rows
    for each of its outcomes that doesn't already have one.
    """
    summary = {
        "scanned_workflows": 0,
        "inserted_rows": 0,
        "skipped_existing": 0,
        "errors": 0,
        "by_terminal_status": {},
    }
    where_clause = "WHERE w.status IN ({statuses})".format(
        statuses=",".join(f"'{s}'" for s in TERMINAL_STATUSES)
    )
    if not backfill_all:
        cutoff = (datetime.now() - timedelta(days=since_days)).isoformat(timespec="seconds")
        where_clause += f" AND w.updated_at >= '{cutoff}'"

    try:
        # Find every (outcome, workflow_terminal_status) pair that doesn't
        # already have a cost_attribution row.
        rows = con.execute(
            f"""
            SELECT o.id AS outcome_id, o.workflow_id, w.kind AS workflow_kind,
                   w.status AS terminal_status
              FROM outcomes o
              JOIN workflows w ON w.id = o.workflow_id
              {where_clause}
               AND NOT EXISTS (
                 SELECT 1 FROM cost_attribution ca
                  WHERE ca.outcome_id = o.id
               )
             LIMIT 50000
            """
        ).fetchall()
    except sqlite3.OperationalError as e:
        summary["errors"] += 1
        summary["error"] = str(e)
        return summary

    summary["scanned_workflows"] = len(rows)
    if not live:
        # Just count by terminal status
        for r in rows:
            ts = TERMINAL_STATUSES.get(r["terminal_status"], "unknown")
            summary["by_terminal_status"][ts] = summary["by_terminal_status"].get(ts, 0) + 1
        return summary

    now = _now_iso()
    for r in rows:
        try:
            terminal = TERMINAL_STATUSES.get(r["terminal_status"], r["terminal_status"])
            con.execute(
                """
                INSERT OR IGNORE INTO cost_attribution
                    (outcome_id, workflow_id, workflow_kind, terminal_status,
                     converted_to_revenue, revenue_usd, attributed_at, updated_at)
                VALUES (?, ?, ?, ?, 0, 0.0, ?, ?)
                """,
                (r["outcome_id"], r["workflow_id"] or "",
                 r["workflow_kind"] or "", terminal, now, now),
            )
            summary["inserted_rows"] += 1
            summary["by_terminal_status"][terminal] = summary["by_terminal_status"].get(terminal, 0) + 1
        except sqlite3.OperationalError as e:
            summary["errors"] += 1
            _log({"phase": "insert", "outcome_id": r["outcome_id"], "error": str(e)[:200]})
    if live:
        con.commit()
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since-days", type=int, default=30)
    ap.add_argument("--backfill-all", action="store_true")
    args = ap.parse_args()
    live = os.getenv("RICK_ATTRIBUTE_COSTS_LIVE", "1").strip().lower() in ("1", "true", "yes") and not args.dry_run

    con = connect()
    try:
        summary = attribute(con, args.since_days, args.backfill_all, live)
    finally:
        con.close()

    summary["live"] = live
    summary["since_days"] = args.since_days
    summary["backfill_all"] = args.backfill_all
    print(json.dumps(summary, indent=2))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
