#!/usr/bin/env python3
"""Hourly + 24h bounce-rate guardian.

Reads the last 1h AND last 24h of:
  - ~/rick-vault/operations/email-sends.jsonl
  - ~/rick-vault/operations/email-bounces.jsonl

Computes bounce rates and writes passive auto-throttle records when a rate
breaches an emergency threshold.

1h window thresholds:
  - bounce_rate_1h > 5%  → kill-switch + pause email channel (6h)
  - bounce_rate_1h > 10% → notify operator

24h window thresholds (new — catches slow trickle bounces):
  - bounce_rate_24h > 5% → kill-switch + pause email channel (6h)
  - bounce_rate_24h > 10% → notify operator

Hard constraints:
  - auto_resume is always False — manual flip required
  - does NOT cancel workflows, only pauses the email channel send gate

2026-07-14 hardening (false 100%-on-1-send pause):
  - below MIN_SENDS_* sends in a window: may alert, never auto-pauses
  - denominator uses Resend's own processed-send count for the window (from
    the resend-bounce-poll poll.done sentinel) when fresh — bounces are polled
    from ALL Resend traffic, while email-sends.jsonl misses bypass send paths,
    so the ledger alone undercounts the denominator

LaunchAgent: ai.rick.bounce-rate-guardian.plist  (StartInterval: 3600)
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

# 1h window
WINDOW_1H = 1
THROTTLE_PCT_1H = 5.0
ALERT_PCT_1H = 10.0

# 24h window (NEW — catches trickle bounces spread over a day)
WINDOW_24H = 24
THROTTLE_PCT_24H = 5.0   # >5% over 24h → 6h pause
ALERT_PCT_24H = 10.0
PAUSE_HOURS_24H = 6      # shorter pause when triggered by 24h window

# Minimum sends in the window before a rate is meaningful. Percentages on
# tiny denominators are noise: bounces are counted by when the POLL records
# them, not when the original send happened, so after downtime a stale bounce
# lands in a near-empty window (2026-06-13 and twice on 2026-07-14: 1 send /
# 1 unrelated bounce = "100%" → hourly re-pause, blocking a paying customer's
# access email). Below the floor: log + alert on any bounce, never auto-pause.
MIN_SENDS_1H = int(os.getenv("RICK_BOUNCE_GUARDIAN_MIN_SENDS_1H", "5"))
MIN_SENDS_24H = int(os.getenv("RICK_BOUNCE_GUARDIAN_MIN_SENDS_24H", "10"))

# Resend-side send counts (poll.done sentinel) older than this are ignored —
# the poll runs every 5 min, so a stale sentinel means the poll is dead and
# its window counts no longer describe the current window.
RESEND_COUNTS_MAX_AGE_HOURS = 1


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


def _pause_email_channel_db(reason: str, hours: int) -> str:
    """Pause email channel in rick-runtime.db channel_state table."""
    import sqlite3
    from datetime import datetime, timezone, timedelta

    db_paths = [
        Path.home() / "rick-vault" / "runtime" / "rick-runtime.db",
        Path.home() / ".openclaw" / "workspace" / "runtime" / "rick-runtime.db",
    ]
    pause_until = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for db_path in db_paths:
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """UPDATE channel_state
                   SET status='paused', paused_until=?, pause_reason=?, updated_at=?
                   WHERE channel='email'""",
                (pause_until, reason, now),
            )
            conn.commit()
            conn.close()
            return "email"
        except Exception:
            continue
    return ""


def _notify_operator(message: str, kind: str) -> None:
    """Best-effort operator notification via runtime engine."""
    try:
        sys.path.insert(0, str(Path.home() / ".openclaw" / "workspace"))
        from runtime.db import connect
        from runtime.engine import notify_operator_deduped

        conn = connect()
        try:
            notify_operator_deduped(
                conn,
                message,
                kind=kind,
                dedup_window_hours=6,
                purpose="ops",
            )
        finally:
            conn.close()
    except Exception:
        pass


def _latest_resend_counts() -> tuple[int | None, int | None]:
    """Return the freshest (resend_sends_1h, resend_sends_24h) sentinel counts.

    resend-bounce-poll.py records in its poll.done sentinel how many emails
    Resend itself processed per window. (None, None) when no fresh sentinel
    carries them (older poll version, dead poll, or its list fetch failed).
    """
    best_dt: datetime | None = None
    best: tuple[int | None, int | None] = (None, None)
    for row in _iter_jsonl(BOUNCES_FILE):
        if row.get("event") != "poll.done" or not isinstance(row.get("resend_sends_24h"), int):
            continue
        dt = _parse_ts(row.get("ts"))
        if dt is None:
            continue
        if best_dt is None or dt > best_dt:
            best_dt = dt
            best = (row.get("resend_sends_1h"), row.get("resend_sends_24h"))
    if best_dt is None:
        return (None, None)
    if datetime.now(timezone.utc) - best_dt > timedelta(hours=RESEND_COUNTS_MAX_AGE_HOURS):
        return (None, None)
    return best


def _compute_window(window_hours: int, resend_sends: int | None = None) -> tuple[int, int, float, int]:
    """Return (sends_count, bounces_count, bounce_rate_pct, ledger_sends).

    Matched universes: bounces are polled from ALL Resend traffic, but the
    email-sends.jsonl ledger misses bypass send paths (raw-curl crons; the
    outbox drain until 2026-07-14) — a lone stale bounce over a near-empty
    ledger window read as "100%". When Resend's own processed-send count for
    the window is available, the denominator is max(ledger, resend): resend is
    the universe bounces come from; ledger can briefly exceed it for mail sent
    since the last poll tick.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    sends = _window_rows(SENDS_FILE, cutoff)
    bounces = [r for r in _window_rows(BOUNCES_FILE, cutoff) if r.get("event") == "bounced"]
    ledger_sends = sum(1 for r in sends if r.get("status") == "sent")
    sends_count = ledger_sends
    if isinstance(resend_sends, int) and resend_sends > sends_count:
        sends_count = resend_sends
    bounces_count = len(bounces)
    rate = (100.0 * bounces_count / sends_count) if sends_count else 0.0
    return sends_count, bounces_count, rate, ledger_sends


def main() -> int:
    resend_1h, resend_24h = _latest_resend_counts()

    # --- 1h window ---
    sends_1h, bounces_1h, rate_1h, ledger_sends_1h = _compute_window(WINDOW_1H, resend_1h)
    cutoff_1h = datetime.now(timezone.utc) - timedelta(hours=WINDOW_1H)
    sends_1h_rows = _window_rows(SENDS_FILE, cutoff_1h)
    source_pipeline = _latest_source_pipeline([r for r in sends_1h_rows if r.get("status") == "sent"])

    throttle_1h = rate_1h > THROTTLE_PCT_1H and sends_1h >= MIN_SENDS_1H
    alert_1h = rate_1h > ALERT_PCT_1H
    paused_channel = ""

    if rate_1h > THROTTLE_PCT_1H and sends_1h < MIN_SENDS_1H:
        _append_jsonl(GUARDIAN_LOG, {
            "ts": now_iso(), "action": "throttle-skipped-low-volume",
            "trigger_window": "1h", "sends": sends_1h, "bounces": bounces_1h,
            "bounce_rate_pct": round(rate_1h, 2), "min_sends": MIN_SENDS_1H,
        })

    if throttle_1h:
        record = {
            "ts": now_iso(),
            "action": "auto-throttle",
            "trigger_window": "1h",
            "reason": f"1h bounce_rate {rate_1h:.1f}% exceeded {THROTTLE_PCT_1H}% threshold",
            "source_pipeline": source_pipeline,
            "channel": "email",
            "window_hours": WINDOW_1H,
            "sends": sends_1h,
            "bounces": bounces_1h,
            "bounce_rate_pct": round(rate_1h, 2),
            "pause_hours": 24,
            "auto_resume": False,
        }
        _append_jsonl(KILLSWITCH_LOG, record)
        paused_channel = _pause_email_channel_db(record["reason"], hours=24)

    if alert_1h:
        # Only claim "manual resume required" when this run actually paused the
        # channel — the 2026-07-14 heartbeat containment re-paused an
        # operator-resumed channel purely off that phrase in a no-pause alert.
        tail_1h = (
            "Channel paused — manual resume required."
            if paused_channel
            else "No auto-pause this run; channel state unchanged."
        )
        _notify_operator(
            f"🚨 Bounce guardian (1h): {rate_1h:.1f}% bounce rate "
            f"({bounces_1h}/{sends_1h}) on {source_pipeline}. "
            f"Auto-throttle={'yes' if throttle_1h else 'no'}. {tail_1h}",
            kind=f"bounce_rate_guardian_1h_{source_pipeline}",
        )

    # --- 24h window (NEW) ---
    sends_24h, bounces_24h, rate_24h, ledger_sends_24h = _compute_window(WINDOW_24H, resend_24h)
    throttle_24h = (not throttle_1h) and (rate_24h > THROTTLE_PCT_24H) and sends_24h >= MIN_SENDS_24H
    alert_24h = rate_24h > ALERT_PCT_24H

    if (not throttle_1h) and rate_24h > THROTTLE_PCT_24H and sends_24h < MIN_SENDS_24H:
        _append_jsonl(GUARDIAN_LOG, {
            "ts": now_iso(), "action": "throttle-skipped-low-volume",
            "trigger_window": "24h", "sends": sends_24h, "bounces": bounces_24h,
            "bounce_rate_pct": round(rate_24h, 2), "min_sends": MIN_SENDS_24H,
        })

    if throttle_24h:
        from datetime import datetime as _dt
        pause_until_ts = (_dt.now(timezone.utc) + timedelta(hours=PAUSE_HOURS_24H)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        record_24h = {
            "ts": now_iso(),
            "action": "auto-throttle",
            "trigger_window": "24h",
            "reason": f"24h bounce_rate {rate_24h:.1f}% exceeded {THROTTLE_PCT_24H}% threshold",
            "source_pipeline": source_pipeline,
            "channel": "email",
            "window_hours": WINDOW_24H,
            "sends": sends_24h,
            "bounces": bounces_24h,
            "bounce_rate_pct": round(rate_24h, 2),
            "pause_hours": PAUSE_HOURS_24H,
            "pause_until": pause_until_ts,
            "auto_resume": False,
        }
        _append_jsonl(KILLSWITCH_LOG, record_24h)
        if not paused_channel:
            paused_channel = _pause_email_channel_db(record_24h["reason"], hours=PAUSE_HOURS_24H)

    if alert_24h and not alert_1h:
        tail_24h = (
            "Channel paused — manual resume required."
            if paused_channel
            else "No auto-pause this run; channel state unchanged."
        )
        _notify_operator(
            f"🚨 Bounce guardian (24h): {rate_24h:.1f}% bounce rate "
            f"({bounces_24h}/{sends_24h}) over last 24h on {source_pipeline}. "
            f"Auto-throttle={'yes' if throttle_24h else 'no'}. {tail_24h}",
            kind=f"bounce_rate_guardian_24h_{source_pipeline}",
        )

    summary = {
        "ts": now_iso(),
        "event": "run.done",
        # 1h
        "sends_1h": sends_1h,
        "bounces_1h": bounces_1h,
        "bounce_rate_1h": round(rate_1h, 2),
        "would_throttle_1h": throttle_1h,
        "would_alert_1h": alert_1h,
        # 24h
        "sends_24h": sends_24h,
        "bounces_24h": bounces_24h,
        "bounce_rate_24h": round(rate_24h, 2),
        "would_throttle_24h": throttle_24h,
        "would_alert_24h": alert_24h,
        # shared
        "source_pipeline": source_pipeline,
        "paused_channel": paused_channel,
        # denominator audit trail: what each universe reported (resend_* is
        # null when no fresh poll sentinel carried counts)
        "ledger_sends_1h": ledger_sends_1h,
        "resend_sends_1h": resend_1h,
        "ledger_sends_24h": ledger_sends_24h,
        "resend_sends_24h": resend_24h,
    }
    _append_jsonl(GUARDIAN_LOG, summary)

    print(
        f"1h:  rate={rate_1h:.2f}% sends={sends_1h} bounces={bounces_1h} "
        f"throttle={'yes' if throttle_1h else 'no'}\n"
        f"24h: rate={rate_24h:.2f}% sends={sends_24h} bounces={bounces_24h} "
        f"throttle={'yes' if throttle_24h else 'no'}\n"
        f"paused_channel={paused_channel or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
