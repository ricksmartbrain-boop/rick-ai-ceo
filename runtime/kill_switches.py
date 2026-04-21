#!/usr/bin/env python3
"""Per-channel outbound safety gate.

Every outbound dispatcher call must pass through assert_channel_active
before sending. The function raises ChannelPaused if:
  - RICK_OUTBOUND_ENABLED=0 in env (master panic button)
  - config/channel-limits.json has the channel marked active=false
  - channel_state row shows status='paused' or 'disabled'
  - paused_until is in the future
  - current time is inside quiet_hours window

Call record_send / record_failure / record_bounce from the dispatcher
after each attempt so this module can auto-pause on thresholds.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
CHANNEL_LIMITS_FILE = Path(
    os.getenv("RICK_CHANNEL_LIMITS_FILE", str(ROOT_DIR / "config" / "channel-limits.json"))
)


class ChannelPaused(Exception):
    """Raised when a channel is not allowed to send right now."""

    def __init__(self, channel: str, reason: str):
        self.channel = channel
        self.reason = reason
        super().__init__(f"channel {channel!r} paused: {reason}")


def _load_limits() -> dict[str, Any]:
    if not CHANNEL_LIMITS_FILE.exists():
        return {"channels": {}}
    try:
        return json.loads(CHANNEL_LIMITS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"channels": {}}


def channel_config(channel: str) -> dict[str, Any]:
    """Return the config block for a channel, or an empty dict if unknown."""
    return _load_limits().get("channels", {}).get(channel, {})


def _now() -> datetime:
    return datetime.now()


def _today_str() -> str:
    return _now().strftime("%Y-%m-%d")


def _ensure_row(conn: sqlite3.Connection, channel: str) -> sqlite3.Row:
    """Ensure a channel_state row exists; return the current row."""
    row = conn.execute("SELECT * FROM channel_state WHERE channel=?", (channel,)).fetchone()
    if row is not None:
        return row
    conn.execute(
        """
        INSERT OR IGNORE INTO channel_state (channel, status, updated_at)
        VALUES (?, 'active', ?)
        """,
        (channel, _now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return conn.execute("SELECT * FROM channel_state WHERE channel=?", (channel,)).fetchone()


def _inside_quiet_hours(cfg: dict[str, Any]) -> bool:
    qh = cfg.get("quiet_hours") or {}
    if "start" not in qh or "end" not in qh:
        return False
    now_h = _now().hour
    start = int(qh["start"])
    end = int(qh["end"])
    if start == end:
        return False
    # Window spans midnight when start > end.
    if start > end:
        return now_h >= start or now_h < end
    return start <= now_h < end


def assert_channel_active(conn: sqlite3.Connection, channel: str) -> None:
    """Raise ChannelPaused if the channel can't send right now."""
    if os.getenv("RICK_OUTBOUND_ENABLED", "1") == "0":
        raise ChannelPaused(channel, "master kill: RICK_OUTBOUND_ENABLED=0")

    cfg = channel_config(channel)
    if not cfg:
        raise ChannelPaused(channel, "unknown channel (not in config/channel-limits.json)")
    if cfg.get("active") is False:
        raise ChannelPaused(channel, cfg.get("_disabled_reason") or "config marked inactive")

    row = _ensure_row(conn, channel)
    status = row["status"]
    if status in ("paused", "disabled"):
        paused_until = row["paused_until"]
        if status == "paused" and paused_until:
            try:
                if datetime.fromisoformat(paused_until) <= _now():
                    # Soft pause expired — flip back to active.
                    conn.execute(
                        "UPDATE channel_state SET status='active', paused_until=NULL, pause_reason='', updated_at=? WHERE channel=?",
                        (_now().isoformat(timespec="seconds"), channel),
                    )
                    conn.commit()
                else:
                    raise ChannelPaused(channel, f"soft-paused until {paused_until} ({row['pause_reason']})")
            except ValueError:
                raise ChannelPaused(channel, f"soft-paused (malformed until): {row['pause_reason']}")
        else:
            raise ChannelPaused(channel, f"{status}: {row['pause_reason']}")

    if _inside_quiet_hours(cfg):
        raise ChannelPaused(channel, "quiet hours")

    # Daily cap check — we compare against updated_at date; if stale, reset counter.
    daily_cap = int(cfg.get("daily") or 0)
    if daily_cap > 0:
        last_send_at = row["last_send_at"]
        if last_send_at and last_send_at[:10] != _today_str():
            # Stale counter from yesterday → reset.
            conn.execute(
                "UPDATE channel_state SET sends_today=0, sends_this_minute=0, updated_at=? WHERE channel=?",
                (_now().isoformat(timespec="seconds"), channel),
            )
            conn.commit()
            row = _ensure_row(conn, channel)
        if int(row["sends_today"] or 0) >= daily_cap:
            raise ChannelPaused(channel, f"daily cap reached ({daily_cap})")

    # Per-minute cap check — we compare against last_send_at minute; if stale, reset.
    per_minute_cap = int(cfg.get("per_minute") or 0)
    if per_minute_cap > 0:
        last_send_at = row["last_send_at"]
        if last_send_at:
            try:
                last = datetime.fromisoformat(last_send_at)
                if (_now() - last).total_seconds() >= 60:
                    conn.execute(
                        "UPDATE channel_state SET sends_this_minute=0, updated_at=? WHERE channel=?",
                        (_now().isoformat(timespec="seconds"), channel),
                    )
                    conn.commit()
                    row = _ensure_row(conn, channel)
            except ValueError:
                pass
        if int(row["sends_this_minute"] or 0) >= per_minute_cap:
            raise ChannelPaused(channel, f"per-minute cap reached ({per_minute_cap})")


def record_send(conn: sqlite3.Connection, channel: str) -> None:
    """Call after a successful send to bump counters."""
    _ensure_row(conn, channel)
    conn.execute(
        """
        UPDATE channel_state
           SET sends_today = sends_today + 1,
               sends_this_minute = sends_this_minute + 1,
               last_send_at = ?,
               auth_failure_streak = 0,
               updated_at = ?
         WHERE channel = ?
        """,
        (_now().isoformat(timespec="seconds"), _now().isoformat(timespec="seconds"), channel),
    )
    conn.commit()


def record_auth_failure(conn: sqlite3.Connection, channel: str, detail: str = "") -> None:
    """Increment auth failure streak. Auto-pause at config threshold."""
    row = _ensure_row(conn, channel)
    new_streak = int(row["auth_failure_streak"] or 0) + 1
    cfg = channel_config(channel)
    threshold = int(cfg.get("auth_failure_pause_after") or 0)
    if threshold > 0 and new_streak >= threshold:
        # Hard pause with reason; needs manual re-auth.
        conn.execute(
            """
            UPDATE channel_state
               SET status='paused', pause_reason=?,
                   paused_until=?,
                   auth_failure_streak=?, updated_at=?
             WHERE channel=?
            """,
            (
                f"auth failure streak = {new_streak}: {detail[:200]}",
                (_now() + timedelta(hours=24)).isoformat(timespec="seconds"),
                new_streak,
                _now().isoformat(timespec="seconds"),
                channel,
            ),
        )
    else:
        conn.execute(
            "UPDATE channel_state SET auth_failure_streak=?, updated_at=? WHERE channel=?",
            (new_streak, _now().isoformat(timespec="seconds"), channel),
        )
    conn.commit()


def record_bounce(conn: sqlite3.Connection, channel: str) -> None:
    """Email bounce — bump counter, pause channel if threshold exceeded."""
    row = _ensure_row(conn, channel)
    new_count = int(row["bounce_count_7d"] or 0) + 1
    cfg = channel_config(channel)
    threshold_pct = float(cfg.get("bounce_threshold_pct") or 0)
    sends_today = int(row["sends_today"] or 0)
    # Simple heuristic: if today's bounces exceed threshold% of today's sends, pause.
    if sends_today > 20 and threshold_pct > 0:
        bounce_rate = (new_count / max(1, sends_today)) * 100.0
        if bounce_rate > threshold_pct:
            conn.execute(
                """
                UPDATE channel_state
                   SET status='paused', pause_reason=?,
                       paused_until=?, bounce_count_7d=?, updated_at=?
                 WHERE channel=?
                """,
                (
                    f"bounce rate {bounce_rate:.1f}% > {threshold_pct}%",
                    (_now() + timedelta(hours=24)).isoformat(timespec="seconds"),
                    new_count,
                    _now().isoformat(timespec="seconds"),
                    channel,
                ),
            )
            conn.commit()
            return
    conn.execute(
        "UPDATE channel_state SET bounce_count_7d=?, updated_at=? WHERE channel=?",
        (new_count, _now().isoformat(timespec="seconds"), channel),
    )
    conn.commit()


def force_pause(conn: sqlite3.Connection, channel: str, reason: str, hours: int = 24) -> None:
    """Admin / emergency pause for a channel."""
    _ensure_row(conn, channel)
    conn.execute(
        """
        UPDATE channel_state
           SET status='paused', pause_reason=?, paused_until=?, updated_at=?
         WHERE channel=?
        """,
        (
            reason[:500],
            (_now() + timedelta(hours=hours)).isoformat(timespec="seconds"),
            _now().isoformat(timespec="seconds"),
            channel,
        ),
    )
    conn.commit()


def force_resume(conn: sqlite3.Connection, channel: str) -> None:
    """Admin resume — clears pause state."""
    _ensure_row(conn, channel)
    conn.execute(
        """
        UPDATE channel_state
           SET status='active', paused_until=NULL, pause_reason='',
               auth_failure_streak=0, updated_at=?
         WHERE channel=?
        """,
        (_now().isoformat(timespec="seconds"), channel),
    )
    conn.commit()


def channel_snapshot(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return a list of every channel + its current state. Useful for the
    weekly roundup and any admin ops dashboards."""
    rows = conn.execute(
        """
        SELECT channel, status, sends_today, sends_this_minute, last_send_at,
               paused_until, pause_reason, bounce_count_7d, auth_failure_streak
          FROM channel_state
         ORDER BY channel
        """
    ).fetchall()
    return [dict(r) for r in rows]
