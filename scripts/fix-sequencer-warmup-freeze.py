#!/usr/bin/env python3
"""
fix-sequencer-warmup-freeze.py — one-shot DB surgery to unfreeze the warmup sequencer.

Root causes fixed:
  1. 5 cold-email-pending workflows stuck: email-cold-1 outbound_job=done but
     - touch_log status='queued' (never updated to 'sent')
     - seq.sequence_started_at missing (so days_since_start()=0 forever)
     - stage never advanced from cold-email-pending → sequence-active
     Result: tick() breaks at voice-day3 (days=0 < 3) every cycle. Permanent freeze.

  2. 27 sequence-active workflows: email-cold-1 touch_log status='queued' instead of 'sent'.
     Result: _has_sent_touch() returns False → Day 5+ email touches blocked when due.

Fix:
  - For cold-email-pending: set sequence_started_at from original sent_at, flip status→sent,
    advance stage to sequence-active.
  - For sequence-active: flip email-cold-1 touch_log status→sent.
  - Hard constraint: ONLY update workflows where outbound_job for email-cold-1 is status=done.
    Never flip status for jobs that are still queued/failed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB = os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db"))

DRY_RUN = "--dry-run" in sys.argv


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def job_is_done(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT status FROM outbound_jobs WHERE id=?", (job_id,)).fetchone()
    return row is not None and row[0] == "done"


def main() -> None:
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row

    fixed_pending = 0
    skipped_pending = 0
    fixed_active = 0

    print(f"{'[DRY-RUN] ' if DRY_RUN else ''}Fixing warmup sequencer freeze...")
    print(f"DB: {DB}\n")

    # -----------------------------------------------------------------------
    # Fix 1: cold-email-pending workflows
    # -----------------------------------------------------------------------
    rows = conn.execute("""
        SELECT id, context_json FROM workflows
         WHERE kind='qualified_lead' AND stage='cold-email-pending' AND status='active'
    """).fetchall()

    print(f"cold-email-pending candidates: {len(rows)}")
    for r in rows:
        wf_id = r["id"]
        ctx = json.loads(r["context_json"] or "{}")
        seq = ctx.setdefault("seq", {})
        tl = seq.setdefault("touch_log", [])

        # Find the email-cold-1 entry
        ec1 = next((e for e in tl if e.get("kind") == "email-cold-1"), None)
        if not ec1:
            print(f"  SKIP {wf_id[:16]} — no email-cold-1 touch_log entry")
            skipped_pending += 1
            continue

        if ec1.get("status") != "queued":
            print(f"  SKIP {wf_id[:16]} — email-cold-1 status={ec1.get('status')} (not queued)")
            skipped_pending += 1
            continue

        # Verify outbound_job is actually done before flipping
        job_ids = ec1.get("outbound_job_ids") or []
        confirmed_done = any(job_is_done(conn, jid) for jid in job_ids)
        if not confirmed_done:
            print(f"  SKIP {wf_id[:16]} — outbound_job not done (ids={job_ids})")
            skipped_pending += 1
            continue

        # Set sequence_started_at from original sent_at
        original_sent_at = ec1.get("sent_at", now_iso())
        seq["sequence_started_at"] = original_sent_at

        # Flip touch_log status
        ec1["status"] = "sent"
        ec1["status_fixed_by"] = "fix-sequencer-warmup-freeze.py"
        ec1["status_fixed_at"] = now_iso()

        days = (datetime.now() - datetime.fromisoformat(original_sent_at)).days
        print(f"  FIX  {wf_id[:16]} — stage→sequence-active, started={original_sent_at[:10]}, days={days}")

        if not DRY_RUN:
            conn.execute(
                "UPDATE workflows SET stage='sequence-active', context_json=?, updated_at=? WHERE id=?",
                (json.dumps(ctx), now_iso(), wf_id),
            )
        fixed_pending += 1

    # -----------------------------------------------------------------------
    # Fix 2: sequence-active workflows (flip queued→sent)
    # -----------------------------------------------------------------------
    rows2 = conn.execute("""
        SELECT id, context_json FROM workflows
         WHERE kind='qualified_lead' AND stage='sequence-active' AND status='active'
    """).fetchall()

    print(f"\nsequence-active candidates: {len(rows2)}")
    for r in rows2:
        wf_id = r["id"]
        ctx = json.loads(r["context_json"] or "{}")
        seq = ctx.get("seq", {})
        changed = False

        for entry in seq.get("touch_log", []):
            if entry.get("kind") == "email-cold-1" and entry.get("status") == "queued":
                # Verify outbound_job=done
                job_ids = entry.get("outbound_job_ids") or []
                if not any(job_is_done(conn, jid) for jid in job_ids):
                    continue
                entry["status"] = "sent"
                entry["status_fixed_by"] = "fix-sequencer-warmup-freeze.py"
                entry["status_fixed_at"] = now_iso()
                changed = True

        if changed:
            if not DRY_RUN:
                conn.execute(
                    "UPDATE workflows SET context_json=?, updated_at=? WHERE id=?",
                    (json.dumps(ctx), now_iso(), wf_id),
                )
            fixed_active += 1

    print(f"  Fixed: {fixed_active} / {len(rows2)}")

    if not DRY_RUN:
        conn.commit()
        print("\n✅ Committed.")
    else:
        print("\n[DRY-RUN] No changes written.")

    conn.close()

    print(f"\nSummary:")
    print(f"  cold-email-pending fixed: {fixed_pending}")
    print(f"  cold-email-pending skipped: {skipped_pending}")
    print(f"  sequence-active fixed (touch_log): {fixed_active}")


if __name__ == "__main__":
    main()
