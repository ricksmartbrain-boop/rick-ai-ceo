#!/usr/bin/env python3
"""TIER-3.5 #A5 — Adaptive follow-up scheduler for inbound replies.

Three phases per run (default daily 09:00 local):

  SCAN  — every email_threads row that's awaiting reply (last_outbound_at >
          last_inbound_at, no pending queue row) gets enqueued with intent-
          based cadence: cold=7d, warm=4d, hot=2d, engaged-then-silent=1d.

  DRAIN — every follow_up_queue row WHERE status='pending' AND
          follow_up_at <= now() AND attempts < max_attempts gets a draft
          generated (writing route). Output to
          ~/rick-vault/mailbox/drafts/follow-up/<thread>-attempt-N.json.
          status → 'draft_ready'. Vlad reviews via /inbox Telegram (separate
          ship). NEVER auto-sends — no send-API code imported into this script.

  CLOSE — attempts >= max_attempts OR age > 30d → status='closed_lost'
          on both queue + email_threads.

DRY-RUN default. Live via RICK_FOLLOWUP_LIVE=1 (drafts only; never sends).
Per-run hard ceiling 50 drafts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts" / "follow-up"
LOG_FILE = DATA_ROOT / "operations" / "follow-up-scheduler.jsonl"

# Adaptive cadence by intent (days)
CADENCE_DAYS_BY_INTENT = {
    "cold": 7,
    "warm": 4,
    "hot": 2,
    "engaged_silent": 1,
    "engaged-then-silent": 1,  # alias
    None: 7,
    "": 7,
}

MAX_ATTEMPTS_DEFAULT = 4
HARD_CEILING_PER_RUN = 50
CLOSE_AGE_DAYS = 30


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = _now_iso()
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _cadence_days(intent: str | None) -> int:
    return CADENCE_DAYS_BY_INTENT.get(intent or "", 7)


def phase_scan(con: sqlite3.Connection, live: bool) -> dict:
    """Find threads awaiting reply that aren't already in follow_up_queue."""
    summary = {"scanned": 0, "queued": 0, "skipped_already_queued": 0}
    try:
        # Threads where the last outbound is more recent than last inbound
        # (or there's outbound but no inbound yet) AND no pending queue row.
        rows = con.execute(
            """
            SELECT t.id AS db_id, t.thread_id, t.prospect_id, t.last_outbound_at,
                   t.last_inbound_at, t.intent_class, t.status
              FROM email_threads t
             WHERE t.status = 'active'
               AND t.last_outbound_at IS NOT NULL
               AND (t.last_inbound_at IS NULL OR t.last_outbound_at > t.last_inbound_at)
               AND NOT EXISTS (
                 SELECT 1 FROM follow_up_queue q
                  WHERE q.thread_id = t.thread_id
                    AND q.status IN ('pending', 'draft_ready')
               )
             LIMIT 500
            """
        ).fetchall()
        summary["scanned"] = len(rows)
        for r in rows:
            intent = r["intent_class"] or "cold"
            cadence = _cadence_days(intent)
            base_ts = r["last_outbound_at"] or _now_iso()
            try:
                base_dt = datetime.fromisoformat(base_ts.replace("Z", "+00:00"))
                # Strip tzinfo for SQLite text comparison
                if base_dt.tzinfo is not None:
                    base_dt = base_dt.replace(tzinfo=None)
            except (ValueError, TypeError):
                base_dt = datetime.now()
            follow_up_at = (base_dt + timedelta(days=cadence)).isoformat(timespec="seconds")
            if live:
                con.execute(
                    """
                    INSERT INTO follow_up_queue
                        (prospect_id, thread_id, follow_up_at, attempts, max_attempts,
                         last_intent, status, created_at, updated_at)
                    VALUES (?, ?, ?, 0, ?, ?, 'pending', ?, ?)
                    """,
                    (r["prospect_id"], r["thread_id"], follow_up_at,
                     MAX_ATTEMPTS_DEFAULT, intent, _now_iso(), _now_iso()),
                )
                summary["queued"] += 1
            else:
                summary["queued"] += 1  # would-queue
        if live:
            con.commit()
    except sqlite3.OperationalError as e:
        summary["error"] = str(e)
    return summary


def _draft_followup_text(con: sqlite3.Connection, thread_id: str, attempt: int, intent: str) -> tuple[str, str, dict]:
    """Generate a draft follow-up via writing route. Returns (subject, body, meta)."""
    # Pull the most recent outbound + inbound text from triage logs / vault for context
    thread_row = con.execute(
        "SELECT thread_id, subject, root_message_id FROM email_threads WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()
    subject_orig = (thread_row["subject"] if thread_row else "") or "(no subject)"
    subject = f"Re: {subject_orig}" if not subject_orig.lower().startswith("re:") else subject_orig

    prompt = (
        "You are Rick — autonomous AI CEO at meetrick.ai. Compose a SHORT, "
        "founder-direct follow-up email. The recipient hasn't replied to my "
        f"previous note (attempt #{attempt}, intent='{intent}'). Voice: dry "
        "humor, no buzzwords, opinion-first. Reference no specific prior text "
        "(I don't have it loaded — keep generic but warm). Suggest one "
        "concrete next step (15-min call OR specific question). Max 5 sentences. "
        "End with a single CTA. NEVER use 'I hope this finds you well'.\n\n"
        f"Original subject: {subject_orig}\n\n"
        "Output: just the email body, no greeting like 'Hi there,' (I'll add it)."
    )
    fallback = (
        "Quick nudge — wanted to make sure my last note didn't get lost. "
        "Happy to jump on a 15-min call this week if useful. "
        "Otherwise, no worries; just say the word and I'll loop you out.\n\n— Rick"
    )

    try:
        from runtime.llm import generate_text  # noqa: WPS433
        result = generate_text("writing", prompt, fallback)
        body = result.content if hasattr(result, "content") else str(result)
        body = body.strip()[:1200]
        meta = {"route": "writing", "attempt": attempt, "intent": intent}
    except Exception as e:  # noqa: BLE001
        body = fallback
        meta = {"route": "writing", "attempt": attempt, "intent": intent, "fallback": True, "error": str(e)[:200]}

    return subject, body, meta


def phase_drain(con: sqlite3.Connection, live: bool, max_drafts: int) -> dict:
    """Generate drafts for due queue rows."""
    summary = {"due": 0, "drafted": 0, "errors": 0, "ceiling_hit": False}
    try:
        rows = con.execute(
            """
            SELECT id, prospect_id, thread_id, follow_up_at, attempts, max_attempts, last_intent
              FROM follow_up_queue
             WHERE status = 'pending'
               AND follow_up_at <= ?
               AND attempts < max_attempts
             ORDER BY follow_up_at ASC
             LIMIT ?
            """,
            (_now_iso(), min(max_drafts, HARD_CEILING_PER_RUN)),
        ).fetchall()
        summary["due"] = len(rows)
        if not live:
            return summary  # dry-run reports candidates only

        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        for r in rows:
            attempt = (r["attempts"] or 0) + 1
            try:
                subject, body, meta = _draft_followup_text(con, r["thread_id"], attempt, r["last_intent"])
                draft_id = f"fu_{uuid.uuid4().hex[:10]}"
                draft_path = DRAFTS_DIR / f"{r['thread_id'][:40].replace('/','_')}-attempt-{attempt}-{draft_id}.json"
                draft_path.write_text(json.dumps({
                    "draft_id": draft_id,
                    "thread_id": r["thread_id"],
                    "prospect_id": r["prospect_id"],
                    "attempt": attempt,
                    "intent": r["last_intent"],
                    "subject": subject,
                    "body": body,
                    "meta": meta,
                    "created_at": _now_iso(),
                }, indent=2), encoding="utf-8")
                con.execute(
                    "UPDATE follow_up_queue SET attempts = ?, status = 'draft_ready', "
                    "draft_path = ?, updated_at = ? WHERE id = ?",
                    (attempt, str(draft_path), _now_iso(), r["id"]),
                )
                summary["drafted"] += 1
                if summary["drafted"] >= HARD_CEILING_PER_RUN:
                    summary["ceiling_hit"] = True
                    break
            except Exception as e:  # noqa: BLE001
                summary["errors"] += 1
                _log({"phase": "drain", "thread": r["thread_id"], "error": str(e)[:200]})
        if live:
            con.commit()
    except sqlite3.OperationalError as e:
        summary["error"] = str(e)
    return summary


def phase_close(con: sqlite3.Connection, live: bool) -> dict:
    summary = {"closed_queue": 0, "closed_threads": 0}
    try:
        cutoff = (datetime.now() - timedelta(days=CLOSE_AGE_DAYS)).isoformat(timespec="seconds")
        if live:
            cur1 = con.execute(
                "UPDATE follow_up_queue SET status = 'closed_lost', updated_at = ? "
                "WHERE status IN ('pending', 'draft_ready') "
                "  AND (attempts >= max_attempts OR created_at < ?)",
                (_now_iso(), cutoff),
            )
            summary["closed_queue"] = cur1.rowcount
            cur2 = con.execute(
                "UPDATE email_threads SET status = 'closed_lost', updated_at = ? "
                "WHERE status = 'active' AND thread_id IN ("
                "  SELECT thread_id FROM follow_up_queue WHERE status = 'closed_lost'"
                ")",
                (_now_iso(),),
            )
            summary["closed_threads"] = cur2.rowcount
            con.commit()
        else:
            cur = con.execute(
                "SELECT COUNT(*) FROM follow_up_queue "
                "WHERE status IN ('pending', 'draft_ready') "
                "  AND (attempts >= max_attempts OR created_at < ?)",
                (cutoff,),
            )
            summary["closed_queue"] = cur.fetchone()[0]
    except sqlite3.OperationalError as e:
        summary["error"] = str(e)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-drafts", type=int, default=20)
    ap.add_argument("--phase", choices=["scan", "drain", "close", "all"], default="all")
    args = ap.parse_args()
    live = os.getenv("RICK_FOLLOWUP_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run

    con = connect()
    summary: dict = {"live": live, "phase": args.phase}
    try:
        if args.phase in ("scan", "all"):
            summary["scan"] = phase_scan(con, live)
        if args.phase in ("drain", "all"):
            summary["drain"] = phase_drain(con, live, args.max_drafts)
        if args.phase in ("close", "all"):
            summary["close"] = phase_close(con, live)
    finally:
        con.close()

    print(json.dumps(summary, indent=2))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
