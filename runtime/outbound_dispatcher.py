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
import importlib.util
import importlib
import json
import os
import sys
import uuid
from functools import lru_cache
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
WARMUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sender-warmup-schedule.py"


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


@lru_cache(maxsize=1)
def _warmup_module():
    spec = importlib.util.spec_from_file_location("sender_warmup_schedule", WARMUP_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load warmup schedule script: {WARMUP_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _warmup_remaining_today() -> int:
    try:
        mod = _warmup_module()
        return max(0, int(mod.get_today_cap()) - int(mod.sends_today()))
    except Exception:
        return 5


def _mark_touch_sent(conn, workflow_id: str, template_id: str, channel: str, result: dict | None) -> None:
    """Promote the matching workflow touch_log entry to sent on successful send."""
    try:
        row = conn.execute("SELECT context_json FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if not row:
            return
        try:
            ctx = json.loads(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(ctx, dict):
            return
        seq = ctx.get("seq")
        if not isinstance(seq, dict):
            seq = ctx.setdefault("seq", {})
        touch_log = seq.setdefault("touch_log", [])
        now = _now_iso()
        matched = False
        for entry in touch_log:
            if entry.get("kind") == template_id and entry.get("channel") == channel:
                entry["status"] = "sent"
                entry["delivered_at"] = now
                if result and result.get("status"):
                    entry["delivery_result"] = result.get("status")
                if result and result.get("message_id"):
                    entry["message_id"] = result.get("message_id")
                matched = True
                break
        if not matched:
            touch_log.append({
                "kind": template_id,
                "channel": channel,
                "status": "sent",
                "sent_at": now,
                "delivered_at": now,
                "delivery_result": (result or {}).get("status", "sent"),
            })
        conn.execute(
            "UPDATE workflows SET context_json=?, updated_at=? WHERE id=?",
            (json.dumps(ctx), now, workflow_id),
        )
    except Exception:
        return


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
        # 2026-04-25: "unknown channel" is non-recoverable — the channel is
        # not in config/channel-limits.json and never will be without code.
        # Treating it like quiet-hours/rate-limit pauses caused 200 synthetic
        # load-test jobs to loop forever (no attempts++, no MAX_ATTEMPTS exit).
        # Caught by Rick TUI diagnostic 2026-04-25. Mark as skipped (terminal).
        if "unknown channel" in (exc.reason or ""):
            conn.execute(
                """
                UPDATE outbound_jobs
                   SET status='skipped', last_error=?, finished_at=?,
                       attempts=attempts+1
                 WHERE id=?
                """,
                (f"unknown-channel: {exc.reason}"[:500], _now_iso(), job["id"]),
            )
            conn.commit()
            summary["status"] = "skipped"
            summary["reason"] = exc.reason
            return summary
        # Recoverable pauses (quiet hours, rate limit) — reschedule.
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

    # 2026-04-24: Fenix preflight gate. Public artifacts (moltbook/reddit/
    # threads/instagram/linkedin/blog/x) get scanned for trigger keywords
    # (customer names, prices, refund/legal language, founder voice).
    # OBSERVE mode (RICK_FENIX_LIVE!=1, default): logs would-have-been
    # blocks to fenix-observed.jsonl, doesn't gate.
    # LIVE mode (RICK_FENIX_LIVE=1): invokes Fenix LLM on flagged artifacts;
    # block/escalate suppresses the send + notifies Vlad. Approve proceeds.
    try:
        from runtime.fenix_gate import preflight as _fenix_preflight
        gate = _fenix_preflight(conn, channel, payload, job_id=job["id"])
        if gate["action"] != "proceed":
            conn.execute(
                """
                UPDATE outbound_jobs
                   SET status='fenix-blocked',
                       last_error=?,
                       finished_at=?,
                       attempts=attempts+1
                 WHERE id=?
                """,
                (f"fenix-{gate['action']}: {gate['reason'][:400]}", _now_iso(), job["id"]),
            )
            conn.commit()
            summary["status"] = f"fenix-{gate['action']}"
            summary["reason"] = gate["reason"]
            return summary
    except Exception as exc:
        # Observe mode: never let the gate itself break the send pipeline.
        # LIVE mode: a crashed gate must fail CLOSED, not open — otherwise
        # the flag promises protection the pipeline doesn't deliver.
        if os.getenv("RICK_FENIX_LIVE", "").strip() == "1":
            conn.execute(
                """
                UPDATE outbound_jobs
                   SET status='fenix-error',
                       last_error=?,
                       finished_at=?,
                       attempts=attempts+1
                 WHERE id=?
                """,
                (f"fenix gate crashed: {type(exc).__name__}: {exc}"[:400], _now_iso(), job["id"]),
            )
            conn.commit()
            summary["status"] = "fenix-error"
            summary["reason"] = f"fenix gate crashed while live: {type(exc).__name__}"
            return summary

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
        _mark_touch_sent(conn, job["lead_id"], job["template_id"], channel, result)
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
        # 2026-07-19: exclude WS-F touch-ledger rows (runtime/touch_log.py
        # log_touch). Those rows carry payload {to, subject, variant, ...,
        # outbox_file} and NO body — delivery is owned by the outbox pipeline
        # (handle_outbox_send / email-send-outbox.py), which flips them to
        # 'sent' via mark_touch_sent. Draining them here sent nothing and
        # dead-lettered 18 founder touches as "max-attempts: body_md missing",
        # after which mark_touch_sent (WHERE status='queued') could no longer
        # record the real send.
        rows = conn.execute(
            """
            SELECT id, lead_id, channel, template_id, payload_json,
                   status, scheduled_at, attempts
              FROM outbound_jobs
             WHERE status = 'queued' AND scheduled_at <= ?
               AND COALESCE(json_extract(payload_json, '$.outbox_file'), '') = ''
             ORDER BY scheduled_at ASC
             LIMIT ?
            """,
            (_now_iso(), int(batch_size)),
        ).fetchall()
        results = []
        email_remaining = _warmup_remaining_today()
        if dry_run:
            for r in rows:
                results.append({"job_id": r["id"], "channel": r["channel"], "status": "dry-run"})
        else:
            for row in rows:
                if row["channel"] == "email" and email_remaining <= 0:
                    break
                summary = _process_one(conn, row)
                results.append(summary)
                _log({"ran_at": _now_iso(), **summary})
                if row["channel"] == "email" and summary.get("status") == "sent":
                    email_remaining -= 1
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
    # 2026-07-19: `python3 -m runtime.outbound_dispatcher` (launchd
    # ai.rick.outbound) runs this file as __main__; formatters then import a
    # SECOND copy under 'runtime.outbound_dispatcher', whose PermanentError /
    # TransientError are different class objects, so _process_one's except
    # clauses never matched formatter-raised errors — permanent failures
    # (e.g. "body_md missing") fell into the generic retry path and
    # dead-lettered as "max-attempts: ..." instead of "permanent: ...".
    # Alias the module so formatters share THIS module's exception classes.
    sys.modules.setdefault("runtime.outbound_dispatcher", sys.modules[__name__])
    sys.exit(main())
