#!/usr/bin/env python3
"""Deterministic outreach touch ledger (WS-F learning loop, 2026-07-13).

Every cold/warm outreach touch gets ONE row in outbound_jobs so the
learning loop has ground truth: which variant/subject went out on which
channel, and whether it ever got a reply. Pure bookkeeping — zero LLM
calls (owner rule: if code can answer, code answers).

Row contract (channel='email' touches):
  lead_id      = recipient email, lowercased  (deterministic reply-linkage key)
  template_id  = touch template ('campaign-step1', 'pitch:proof_led', ...)
  payload_json = {to, subject, variant, skill, source, outbox_file, workflow_id}
  status       = 'queued' (outbox draft) -> 'sent' (left Rick)
  result_json  = {"outcome": "sent"} -> {"outcome": "replied", "replied_at": ...}

Callers:
  scripts/campaign-engine.py        log_touch(status='sent') after Resend 200
  skill_handlers.handle_pitch_send  log_touch(status='queued', outbox_file=...)
  skill_handlers.handle_outbox_send mark_touch_sent(f.name) / log_touch fallback
  scripts/reply-watcher.py          mark_replied(email) on classified inbound

All writes are shielded by callers — a bookkeeping failure must never
block or fail a send.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any

# Only credit variants on replies newer than this window — a reply to a
# 6-month-old thread says nothing about the subject line.
REPLY_LINK_WINDOW_DAYS = 30


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_touch(
    connection: sqlite3.Connection,
    *,
    to: str,
    channel: str = "email",
    template_id: str = "",
    subject: str = "",
    variant: str = "",
    skill: str = "",
    source: str = "",
    status: str = "sent",
    outbox_file: str = "",
    workflow_id: str = "",
) -> str:
    """Insert one touch row into outbound_jobs. Returns the row id."""
    job_id = f"ob_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    payload = {
        "to": (to or "").strip().lower(),
        "subject": subject[:200],
        "variant": variant,
        "skill": skill,
        "source": source,
        "outbox_file": outbox_file,
        "workflow_id": workflow_id,
    }
    connection.execute(
        """
        INSERT INTO outbound_jobs
          (id, lead_id, channel, template_id, payload_json, status,
           scheduled_at, created_at, finished_at, result_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            (to or "").strip().lower(),
            channel,
            template_id,
            json.dumps(payload),
            status,
            now,
            now,
            now if status == "sent" else None,
            json.dumps({"outcome": status}),
        ),
    )
    connection.commit()
    return job_id


def mark_touch_sent(connection: sqlite3.Connection, outbox_file: str) -> int:
    """Flip the queued touch for this outbox file to sent. Returns rows updated."""
    if not outbox_file:
        return 0
    now = _now_iso()
    cur = connection.execute(
        """
        UPDATE outbound_jobs
           SET status = 'sent',
               finished_at = ?,
               result_json = json_set(COALESCE(result_json, '{}'), '$.outcome', 'sent')
         WHERE status = 'queued'
           AND channel = 'email'
           AND json_extract(payload_json, '$.outbox_file') = ?
        """,
        (now, outbox_file),
    )
    connection.commit()
    return cur.rowcount


def mark_replied(connection: sqlite3.Connection, email: str) -> list[dict[str, Any]]:
    """Link an inbound reply back to the originating touch rows.

    Marks every recent sent email touch to this recipient outcome='replied'
    (idempotent — already-replied rows are skipped) and credits a variant
    win in skill_variants when the touch recorded one. Returns the rows
    updated so callers can log the linkage.
    """
    email = (email or "").strip().lower()
    if not email:
        return []
    now = _now_iso()
    rows = connection.execute(
        """
        SELECT id, template_id, payload_json
          FROM outbound_jobs
         WHERE lead_id = ?
           AND channel = 'email'
           AND status = 'sent'
           AND created_at >= datetime('now', ?)
           AND COALESCE(json_extract(result_json, '$.outcome'), '') != 'replied'
        """,
        (email, f"-{REPLY_LINK_WINDOW_DAYS} day"),
    ).fetchall()
    updated: list[dict[str, Any]] = []
    for row in rows:
        row_id = row["id"] if hasattr(row, "keys") else row[0]
        template_id = row["template_id"] if hasattr(row, "keys") else row[1]
        payload_raw = row["payload_json"] if hasattr(row, "keys") else row[2]
        connection.execute(
            """
            UPDATE outbound_jobs
               SET result_json = json_set(COALESCE(result_json, '{}'),
                                          '$.outcome', 'replied',
                                          '$.replied_at', ?)
             WHERE id = ?
            """,
            (now, row_id),
        )
        try:
            payload = json.loads(payload_raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        skill = payload.get("skill") or ""
        variant = payload.get("variant") or ""
        if skill and variant:
            # A real human reply is the strongest win signal we have —
            # far better than the draft-time quality heuristics.
            try:
                from runtime.variants import record_variant_outcome
                record_variant_outcome(
                    connection, skill, variant,
                    won=True, quality=1.0, cost_usd=0.0,
                )
            except Exception:
                pass
        updated.append({
            "id": row_id,
            "template_id": template_id,
            "variant": variant,
            "skill": skill,
        })
    connection.commit()
    return updated
