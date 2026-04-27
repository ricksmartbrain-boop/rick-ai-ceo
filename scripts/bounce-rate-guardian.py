#!/usr/bin/env python3
"""Hourly bounce-rate guardian.

Reads the last 1h of:
  - ~/rick-vault/operations/email-sends.jsonl
  - ~/rick-vault/operations/email-bounces.jsonl

Computes bounce_rate_1h = bounces / sends and writes a passive auto-throttle
record when the rate breaches the emergency threshold.

If bounce_rate_1h > 5%:
  - append a kill-switch row to ~/rick-vault/operations/pipeline-killswitch.jsonl
  - pause the email channel in runtime.db (best-effort)

If bounce_rate_1h > 10%:
  - notify_operator_deduped() so Vlad sees it in the morning digest

LaunchAgent: ai.rick.bounce-rate-guardian.plist
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"
SENDS_FILE = OPS / "email-sends.jsonl"
BOUNCES_FILE = OPS / "email-bounces.jsonl"
GUARDIAN_LOG = OPS / "bounce-rate-guardian.jsonl"
KILLSWITCH_LOG = OPS / "pipeline-killswitch.jsonl"

WINDOW_HOURS = 1
THROTTLE_THRESHOLD_PCT = 5.0
ALERT_THRESHOLD_PCT = 10.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        ts = str(raw)
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _iter_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row
    except OSError:
        return


def _window_rows(path: Path, cutoff: datetime) -> list[dict]:
    rows: list[dict] = []
    for row in _iter_jsonl(path):
        dt = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("ran_at"))
        if dt is None or dt < cutoff:
            continue
        rows.append(row)
    return rows


def _latest_source_pipeline(send_rows: list[dict]) -> str:
    best: tuple[datetime, str] | None = None
    for row in send_rows:
        dt = _parse_ts(row.get("ts") or row.get("timestamp") or row.get("ran_at"))
        if dt is None:
            continue
        source = (
            row.get("source")
            or row.get("pipeline")
            or row.get("channel")
            or row.get("workflow")
            or row.get("stage")
            or "email"
        )
        source = str(source).strip() or "email"
        if best is None or dt > best[0]:
            best = (dt, source)
    return best[1] if best else "email"


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _pause_email_channel(reason: str) -> str:
    try:
        from runtime.db import connect
        from runtime.kill_switches import force_pause

        conn = connect()
        try:
            force_pause(conn, "email", reason, hours=24)
        finally:
            conn.close()
        return "email"
    except Exception:
        return ""


def main() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    sends = _window_rows(SENDS_FILE, cutoff)
    bounces = [row for row in _window_rows(BOUNCES_FILE, cutoff) if row.get("event") == "bounced"]
    complaints = [row for row in _window_rows(BOUNCES_FILE, cutoff) if row.get("event") == "complained"]

    sends_count = sum(1 for row in sends if row.get("status") == "sent")
    bounces_count = len(bounces)
    complaints_count = len(complaints)
    bounce_rate = (100.0 * bounces_count / sends_count) if sends_count else 0.0
    source_pipeline = _latest_source_pipeline([row for row in sends if row.get("status") == "sent"])

    throttle = bounce_rate > THROTTLE_THRESHOLD_PCT
    alert = bounce_rate > ALERT_THRESHOLD_PCT
    paused_channel = ""

    if throttle:
        record = {
            "ts": now_iso(),
            "action": "auto-throttle",
            "reason": "hourly bounce_rate exceeded 5%",
            "source_pipeline": source_pipeline,
            "channel": "email",
            "window_hours": WINDOW_HOURS,
            "sends_1h": sends_count,
            "bounces_1h": bounces_count,
            "complaints_1h": complaints_count,
            "bounce_rate_1h": round(bounce_rate, 2),
        }
        _append_jsonl(KILLSWITCH_LOG, record)
        paused_channel = _pause_email_channel(record["reason"])

    if alert:
        try:
            from runtime.db import connect
            from runtime.engine import notify_operator_deduped

            conn = connect()
            try:
                notify_operator_deduped(
                    conn,
                    (
                        f"🚨 Bounce guardian: {bounce_rate:.1f}% bounce rate in the last hour "
                        f"({bounces_count}/{sends_count}) on {source_pipeline}. "
                        f"Auto-throttle={'yes' if throttle else 'no'}."
                    ),
                    kind=f"bounce_rate_guardian_{source_pipeline}",
                    dedup_window_hours=6,
                    purpose="ops",
                )
            finally:
                conn.close()
        except Exception:
            pass

    summary = {
        "ts": now_iso(),
        "event": "run.done",
        "window_hours": WINDOW_HOURS,
        "sends_1h": sends_count,
        "bounces_1h": bounces_count,
        "complaints_1h": complaints_count,
        "bounce_rate_1h": round(bounce_rate, 2),
        "source_pipeline": source_pipeline,
        "would_throttle": throttle,
        "would_alert": alert,
        "paused_channel": paused_channel,
    }
    _append_jsonl(GUARDIAN_LOG, summary)

    print(
        f"bounce_rate_1h={bounce_rate:.2f}% sends_1h={sends_count} "
        f"bounces_1h={bounces_count} complaints_1h={complaints_count} "
        f"source_pipeline={source_pipeline} would_throttle={'yes' if throttle else 'no'} "
        f"would_alert={'yes' if alert else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
