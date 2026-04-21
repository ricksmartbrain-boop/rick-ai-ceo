#!/usr/bin/env python3
"""Unified outbound job queue across all Rick channels.

Every outbound touch (email / moltbook DM / reddit comment / linkedin DM /
threads post / instagram post / etc) lands in one `outbound_jobs` table and
drains through this dispatcher every 5 minutes. The dispatcher:

  1. Pulls batch of queued jobs whose scheduled_at has passed
  2. For each job, calls kill_switches.assert_channel_active(channel)
     — if paused, leaves job queued with scheduled_at bumped +1h
  3. Resolves channel → formatter module, calls formatter.send(payload)
  4. On success: status='done', call record_send, increment counters
  5. On auth failure: record_auth_failure (may auto-pause channel)
  6. On other failure: status='queued' with attempts++ up to max retries,
     then status='failed' with last_error

Formatters live in runtime/formatters/<channel>.py and implement:

    def send(payload: dict) -> dict:
        # returns: {"status": "sent", "message_id": "...", ...}
        # OR raises AuthFailure / TransientError / PermanentError

Public entry points:

    fan_out(conn, lead_id, template_id, channels, payload) -> list[job_id]
    drain(conn, batch_size=20) -> dict  # summary stats
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime import kill_switches  # noqa: E402
from runtime.db import connect as db_connect  # noqa: E402


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "outbound-dispatcher.jsonl"
MAX_ATTEMPTS = 4
BACKOFF_SECS = {1: 60, 2: 600, 3: 3600}  # 1m, 10m, 1h


class AuthFailure(Exception):
    """Formatter couldn't authenticate — triggers record_auth_failure + requeue."""


class TransientError(Exception):
    """Temporary failure — retry with backoff."""


class PermanentError(Exception):
    """Dead letter — don't retry."""


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def fan_out(
    conn,
    *,
    lead_id: str,
    template_id: str,
    channels: list[str],
    payload: dict,
    scheduled_at: str | None = None,
) -> list[str]:
    """Create one outbound_job per channel for this lead+template pair.

    Returns list of created job IDs. Duplicate suppression by (lead_id, channel,
    template_id) within the last 7d is handled here — re-sending the same
    template to the same lead on the same channel is blocked to prevent loops.
    """
    created: list[str] = []
    cutoff = (_now() - timedelta(days=7)).isoformat(timespec="seconds")
    for channel in channels:
        # Dedupe: skip if same lead+template+channel job exists in last 7d.
        dup = conn.execute(
            """
            SELECT id FROM outbound_jobs
             WHERE lead_id=? AND channel=? AND template_id=?
               AND created_at >= ?
             LIMIT 1
            """,
            (lead_id, channel, template_id, cutoff),
        ).fetchone()
        if dup:
            continue
        job_id = f"ob_{uuid.uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO outbound_jobs
              (id, lead_id, channel, template_id, payload_json, status,
               scheduled_at, created_at, attempts)
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, 0)
            """,
            (
                job_id, lead_id, channel, template_id,
                json.dumps(payload)[:20000],
                scheduled_at or _now_iso(),
                _now_iso(),
            ),
        )
        created.append(job_id)
    conn.commit()
    return created


def _resolve_formatter(channel: str):
    """Return a callable `send(payload: dict) -> dict` for the channel, or None.

    Lazy-imports runtime/formatters/<channel>.py. If the module is missing
    or has no `send` attribute, returns None so the caller can mark the job
    as skipped without crashing the whole drain loop. Phase E will land the
    actual formatter modules.
    """
    cfg = kill_switches.channel_config(channel)
    mod_path = cfg.get("formatter_module") or f"runtime.formatters.{channel}"
    try:
        mod = importlib.import_module(mod_path)
    except ImportError:
        return None
    return getattr(mod, "send", None)


def _bump_scheduled(attempts: int) -> str:
    """Return a future scheduled_at for retries based on attempts made."""
    secs = BACKOFF_SECS.get(attempts, 3600)
    return (_now() + timedelta(seconds=secs)).isoformat(timespec="seconds")


def _process_one(conn, job) -> dict:
    """Handle a single outbound job. Returns a summary dict."""
    channel = job["channel"]
    summary = {"job_id": job["id"], "channel": channel, "lead_id": job["lead_id"]}
    try:
        kill_switches.assert_channel_active(conn, channel)
    except kill_switches.ChannelPaused as exc:
        # Reschedule instead of failing — channel might come back online.
        conn.execute(
            """
            UPDATE outbound_jobs
               SET scheduled_at=?, last_error=?
             WHERE id=?
            """,
            ((_now() + timedelta(hours=1)).isoformat(timespec="seconds"),
             f"paused: {exc.reason}"[:500], job["id"]),
        )
        conn.commit()
        summary["status"] = "deferred"
        summary["reason"] = exc.reason
        return summary

    send_fn = _resolve_formatter(channel)
    if send_fn is None:
        # No formatter available — not a retryable failure, mark skipped.
        conn.execute(
            """
            UPDATE outbound_jobs
               SET status='skipped', last_error='no formatter module',
                   finished_at=?, attempts=attempts+1
             WHERE id=?
            """,
            (_now_iso(), job["id"]),
        )
        conn.commit()
        summary["status"] = "skipped"
        summary["reason"] = "no formatter module"
        return summary

    try:
        payload = json.loads(job["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}

    attempts = int(job["attempts"] or 0) + 1
    try:
        result = send_fn(payload)
        conn.execute(
            """
            UPDATE outbound_jobs
               SET status='done', finished_at=?, attempts=?, result_json=?
             WHERE id=?
            """,
            (_now_iso(), attempts, json.dumps(result or {})[:8000], job["id"]),
        )
        kill_switches.record_send(conn, channel)
        conn.commit()
        summary["status"] = "sent"
        summary["result"] = (result or {}).get("status") or "sent"
    except AuthFailure as exc:
        kill_switches.record_auth_failure(conn, channel, str(exc))
        conn.execute(
            """
            UPDATE outbound_jobs
               SET scheduled_at=?, attempts=?, last_error=?
             WHERE id=?
            """,
            (_bump_scheduled(attempts), attempts, f"auth: {exc}"[:500], job["id"]),
        )
        conn.commit()
        summary["status"] = "auth-failure"
    except PermanentError as exc:
        conn.execute(
            """
            UPDATE outbound_jobs
               SET status='failed', finished_at=?, attempts=?, last_error=?
             WHERE id=?
            """,
            (_now_iso(), attempts, f"permanent: {exc}"[:500], job["id"]),
        )
        conn.commit()
        summary["status"] = "failed"
        summary["reason"] = str(exc)
    except (TransientError, Exception) as exc:  # noqa: BLE001
        # Retry until MAX_ATTEMPTS then dead-letter.
        if attempts >= MAX_ATTEMPTS:
            conn.execute(
                """
                UPDATE outbound_jobs
                   SET status='failed', finished_at=?, attempts=?, last_error=?
                 WHERE id=?
                """,
                (_now_iso(), attempts, f"max-attempts: {exc}"[:500], job["id"]),
            )
            conn.commit()
            summary["status"] = "failed"
            summary["reason"] = f"max-attempts ({attempts})"
        else:
            conn.execute(
                """
                UPDATE outbound_jobs
                   SET scheduled_at=?, attempts=?, last_error=?
                 WHERE id=?
                """,
                (_bump_scheduled(attempts), attempts, f"retry: {exc}"[:500], job["id"]),
            )
            conn.commit()
            summary["status"] = "retry"
            summary["attempts"] = attempts
    return summary


def drain(conn=None, batch_size: int = 20, dry_run: bool = False) -> dict:
    """Process up to batch_size jobs whose scheduled_at has passed."""
    own_conn = False
    if conn is None:
        conn = db_connect()
        own_conn = True
    try:
        rows = conn.execute(
            """
            SELECT id, lead_id, channel, template_id, payload_json,
                   status, scheduled_at, attempts
              FROM outbound_jobs
             WHERE status = 'queued' AND scheduled_at <= ?
             ORDER BY scheduled_at ASC
             LIMIT ?
            """,
            (_now_iso(), int(batch_size)),
        ).fetchall()
        results = []
        if dry_run:
            for r in rows:
                results.append({"job_id": r["id"], "channel": r["channel"], "status": "dry-run"})
        else:
            for row in rows:
                summary = _process_one(conn, row)
                results.append(summary)
                _log({"ran_at": _now_iso(), **summary})
        return {
            "ran_at": _now_iso(),
            "picked": len(rows),
            "processed": len(results),
            "summary_by_status": _count_by(results, "status"),
            "dry_run": dry_run,
        }
    finally:
        if own_conn:
            conn.close()


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        v = r.get(key) or "unknown"
        out[v] = out.get(v, 0) + 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")

    drain_cmd = sub.add_parser("drain", help="Process queued outbound jobs")
    drain_cmd.add_argument("--batch", type=int, default=20)
    drain_cmd.add_argument("--dry-run", action="store_true")

    status_cmd = sub.add_parser("status", help="Show channel_state snapshot")

    snapshot_cmd = sub.add_parser("queue", help="Show outbound_jobs queue summary")

    args = parser.parse_args()

    conn = db_connect()
    try:
        if args.cmd == "status":
            snap = kill_switches.channel_snapshot(conn)
            print(json.dumps(snap, indent=2))
        elif args.cmd == "queue":
            rows = conn.execute(
                "SELECT status, channel, COUNT(*) AS n FROM outbound_jobs GROUP BY status, channel"
            ).fetchall()
            print(json.dumps([dict(r) for r in rows], indent=2))
        else:
            result = drain(conn, batch_size=args.batch, dry_run=getattr(args, "dry_run", False))
            print(json.dumps(result, indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
