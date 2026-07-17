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
import importlib.util
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
CHANNEL_LIMITS_FILE = Path(
    os.getenv("RICK_CHANNEL_LIMITS_FILE", str(ROOT_DIR / "config" / "channel-limits.json"))
)
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
# Zero-tolerance bounce guardian marker (written by ops scripts).
_BOUNCE_GUARDIAN_FILE = DATA_ROOT / "control" / "email-bounce-guardian.json"


class ChannelPaused(Exception):
    """Raised when a channel is not allowed to send right now."""

    def __init__(self, channel: str, reason: str):
        self.channel = channel
        self.reason = reason
        super().__init__(f"channel {channel!r} paused: {reason}")


# Transactional outbox item `type` values that must NOT wait for the 07:00
# quiet-hours release (2026-07-16: russian@crushermail.com's paid access
# email sat ~2h behind the quiet-hours gate; only luck it wasn't 8h).
# 2026-07-17: the waiver also covers the daily-cap and sender-warmup VOLUME
# clauses — delivery/dunning are 1-2 deduped mails per customer and must
# never strand behind broadcast/outreach volume for the rest of a UTC day
# (the 2026-07-14 stranded-access class). MASTER KILL SWITCH, channel
# pause/disable and per-minute pacing remain ABSOLUTE — the panic button
# stops everything; suppression and is_send_allowed still apply. Marketing
# types (pitch/followup/nurture/cold/welcome/win-back) keep every clause.
# Canonical list — import this, don't scatter string literals.
# "dunning"/"dunning-reminder" are the two payment-fix types stripe-poll's
# dunning machinery emits (day-0 + day-N).
TRANSACTIONAL_EMAIL_TYPES = frozenset({"delivery", "dunning", "dunning-reminder"})


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
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    """Parse a stored ISO timestamp; accepts trailing 'Z' and naive forms."""
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
    # Quiet hours are configured in local time in config/channel-limits.json.
    now_h = datetime.now().astimezone().hour
    start = int(qh["start"])
    end = int(qh["end"])
    if start == end:
        return False
    # Window spans midnight when start > end.
    if start > end:
        return now_h >= start or now_h < end
    return start <= now_h < end


def _email_warmup_cap_status() -> tuple[int | None, int | None, str]:
    """Return (today_cap, today_sent, reason) from the sender warmup ledger."""
    script = ROOT_DIR / "scripts" / "sender-warmup-schedule.py"
    if not script.exists():
        return None, None, "warmup_script_missing"
    try:
        spec = importlib.util.spec_from_file_location("rick_sender_warmup_gate", script)
        if spec is None or spec.loader is None:
            return None, None, "warmup_script_unloadable"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return int(module.get_today_cap()), int(module.sends_today()), "ok"
    except Exception as exc:
        return 0, None, f"warmup_gate_error:{type(exc).__name__}:{exc}"


def assert_channel_active(conn: sqlite3.Connection, channel: str, *, transactional: bool = False) -> None:
    """Raise ChannelPaused if the channel can't send right now.

    transactional=True (item type in TRANSACTIONAL_EMAIL_TYPES) waives the
    quiet-hours, daily-cap and sender-warmup clauses — delivery/dunning are
    1-2 deduped mails per customer, never volume. Master kill, channel
    pause/disable and per-minute pacing are ABSOLUTE and apply to
    transactional too (the panic button must stop everything).
    """
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
                if _parse_iso(paused_until) <= _now():
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

    if not transactional and _inside_quiet_hours(cfg):
        raise ChannelPaused(channel, "quiet hours")

    # Daily cap check — we compare against updated_at date; if stale, reset
    # counter. The stale-reset runs for transactional sends too, so a
    # transactional first-send of a new day can't carry yesterday's count into
    # today's marketing accounting. Only the cap raise is waived (see docstring).
    daily_cap = int(cfg.get("daily") or 0)
    last_send_at = row["last_send_at"]
    if last_send_at and last_send_at[:10] != _today_str():
        # Stale counter from yesterday → reset.
        conn.execute(
            "UPDATE channel_state SET sends_today=0, sends_this_minute=0, updated_at=? WHERE channel=?",
            (_now().isoformat(timespec="seconds"), channel),
        )
        conn.commit()
        row = _ensure_row(conn, channel)
    if daily_cap > 0 and not transactional and int(row["sends_today"] or 0) >= daily_cap:
        raise ChannelPaused(channel, f"daily cap reached ({daily_cap})")

    # Sender-warmup ramp — volume clause: waived for transactional too.
    if channel == "email" and not transactional:
        warmup_cap, warmup_sent, warmup_reason = _email_warmup_cap_status()
        if warmup_cap is None:
            pass
        elif warmup_cap <= 0:
            raise ChannelPaused(channel, f"sender warmup cap reached ({warmup_cap}); {warmup_reason}")
        elif warmup_sent is None:
            raise ChannelPaused(channel, f"sender warmup cap unavailable; {warmup_reason}")
        elif warmup_sent >= warmup_cap:
            raise ChannelPaused(channel, f"sender warmup cap reached ({warmup_cap}); ledger_sent={warmup_sent}")

    # Per-minute cap check — we compare against last_send_at minute; if stale, reset.
    per_minute_cap = int(cfg.get("per_minute") or 0)
    if per_minute_cap > 0:
        last_send_at = row["last_send_at"]
        if last_send_at:
            try:
                last = datetime.fromisoformat(last_send_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
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
    """Email bounce — bump counter, pause channel if threshold exceeded.

    BOUNCE GUARDIAN MODE (Strategy-A ICP batch):
    When email-bounce-guardian.json is active, ANY single bounce immediately
    re-pauses the channel — much tighter than the normal 5%/24h threshold.
    Guardian is time-boxed (active.until) so it expires automatically.
    """
    row = _ensure_row(conn, channel)
    new_count = int(row["bounce_count_7d"] or 0) + 1

    # --- Zero-tolerance guardian check (fires first, channel-specific) ---
    if channel == "email" and _BOUNCE_GUARDIAN_FILE.exists():
        try:
            _g = json.loads(_BOUNCE_GUARDIAN_FILE.read_text(encoding="utf-8"))
            _until_str = _g.get("until", "")
            if _g.get("active") and _until_str and datetime.fromisoformat(_until_str) > _now():
                conn.execute(
                    """
                    UPDATE channel_state
                       SET status='paused', pause_reason=?,
                           paused_until=?, bounce_count_7d=?, updated_at=?
                     WHERE channel=?
                    """,
                    (
                        f"BOUNCE GUARDIAN TRIGGERED: any-bounce zero-tolerance active until {_until_str}",
                        (_now() + timedelta(hours=24)).isoformat(timespec="seconds"),
                        new_count,
                        _now().isoformat(timespec="seconds"),
                        channel,
                    ),
                )
                conn.commit()
                return
        except Exception:
            pass  # Guardian read errors must never block the normal path

    # --- Normal threshold logic ---
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


# ---------------------------------------------------------------------------
# Unified per-recipient send gate (2026-07-13)
#
# Every email send path must call is_send_allowed(to) before sending and log
# 'SEND_BLOCKED reason=<reason> to=<addr>' when it returns (False, reason).
# The channel-level gate (assert_channel_active) stays separate: it needs a
# DB connection and covers pause/caps; this gate is per-recipient and
# dependency-free so even the leanest cron sender can import it.
# ---------------------------------------------------------------------------

RICK_ENV_FILE = Path.home() / "clawd" / "config" / "rick.env"
SUPPRESSION_FILES = (
    DATA_ROOT / "mailbox" / "suppression.txt",
    DATA_ROOT / "control" / "dnc-list.txt",
)
# Send logs the existing senders already write — used for the frequency cap.
SEND_LOG_FILES = (
    DATA_ROOT / "operations" / "email-sends.jsonl",
    DATA_ROOT / "operations" / "email-sequence-send.jsonl",
    DATA_ROOT / "logs" / "pipeline.jsonl",
    DATA_ROOT / "runtime" / "nurture" / "sent.log",
)
FREQUENCY_CAP_DAYS = 7
RECENT_SEND_CAP_MINUTES = 60


def _env_flag(name: str, default: str = "") -> str:
    """RICK_* flag lookup: process env first, then ~/clawd/config/rick.env.

    The rick.env fallback exists because several cron/launchd entry points do
    not source the env file (the 2026-04-23 env-export bug class). Inline
    '# comments' after the value are stripped — flag values only, never use
    this helper for secrets.
    """
    val = os.getenv(name)
    if val is not None and val.strip() != "":
        return val.strip()
    try:
        if RICK_ENV_FILE.exists():
            for raw in RICK_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("export "):
                    line = line[len("export "):]
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].split("#", 1)[0].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def load_merged_suppression() -> set[str]:
    """Union of mailbox/suppression.txt + control/dnc-list.txt, lowercased.

    Entries may be full addresses or domain-level ('@folderly.com').
    Raises OSError if an existing list can't be read — callers inside
    is_send_allowed treat that as fail-closed.
    """
    merged: set[str] = set()
    for path in SUPPRESSION_FILES:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            entry = raw.split("#", 1)[0].strip().lower()
            if entry:
                merged.add(entry)
    return merged


def is_suppressed_address(email: str, merged: set[str] | None = None) -> bool:
    """True when the address (or its @domain) is on either suppression list."""
    target = (email or "").strip().lower()
    if not target:
        return True
    entries = load_merged_suppression() if merged is None else merged
    if target in entries:
        return True
    domain = target.rsplit("@", 1)[-1] if "@" in target else ""
    return bool(domain) and f"@{domain}" in entries


def last_send_ts(email: str) -> datetime | None:
    """Most recent logged send to this address across the known send logs."""
    target = (email or "").strip().lower()
    if not target:
        return None
    latest: datetime | None = None

    def _consider(addr: Any, ts_raw: Any) -> None:
        nonlocal latest
        if str(addr or "").strip().lower() != target or not ts_raw:
            return
        try:
            ts = _parse_iso(str(ts_raw))
        except ValueError:
            return
        if latest is None or ts > latest:
            latest = ts

    for path in SEND_LOG_FILES:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if path.name == "sent.log":
            # nurture sent.log: idem_key \t email \t email_num \t iso_ts
            for line in lines:
                parts = line.split("\t")
                if len(parts) >= 4:
                    _consider(parts[1], parts[3])
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            stage = str(r.get("stage") or "")
            resend_id = str(r.get("resend_id") or "")
            # Failed sends must NOT count as sends: follow-up-automation's
            # log_followup stamps the *_sent stage and stuffs the block
            # reason into resend_id ('channel_paused: ...', 'suppressed: ...',
            # 'SEND_BLOCKED ...'), so those rows describe a send that never
            # happened — counting them blocked the real re-touch for 7 days.
            # Real provider ids are opaque tokens (never a space or colon).
            # Only rows POSITIVELY marked failed are skipped; anything
            # ambiguous still counts (double-send prevention wins — 07-14).
            looks_failed = r.get("status") != "sent" and (
                " " in resend_id or ":" in resend_id
            )
            is_send = not looks_failed and (
                r.get("status") == "sent"
                or bool(resend_id)
                or stage == "contacted"
                or stage.endswith("_sent")
            )
            if is_send:
                _consider(r.get("to") or r.get("email"), r.get("ts") or r.get("timestamp"))
    return latest


def is_send_allowed(email: str, *, cold: bool = True) -> tuple[bool, str]:
    """Unified fail-closed per-recipient send gate. Returns (allowed, reason).

    Checks, in order:
      1. RICK_OUTBOUND_ENABLED master kill (blocked when '0')
      2. RICK_EMAIL_SEND_LIVE live flag (blocked unless '1')
      3. role-account validator (info@/support@/etc.)
      4. merged suppression list (case-insensitive, '@domain' entries match)
      5. recent-send cap: no send to the same address inside 60 minutes
      6. frequency cap: no *cold* send when any logged send to the address
         exists within the last FREQUENCY_CAP_DAYS days (sequence/follow-up
         senders pass cold=False — their own scheduling controls cadence)

    Any internal error fails CLOSED. Callers must log
    'SEND_BLOCKED reason=<reason> to=<email>' when blocked.
    """
    try:
        target = (email or "").strip().lower()
        if not target or "@" not in target:
            return False, f"invalid_recipient:{target!r}"
        if _env_flag("RICK_OUTBOUND_ENABLED", "1") == "0":
            return False, "master_kill:RICK_OUTBOUND_ENABLED=0"
        if _env_flag("RICK_EMAIL_SEND_LIVE", "0") != "1":
            return False, "not_live:RICK_EMAIL_SEND_LIVE!=1"
        try:
            from runtime.email_validator import is_role_account

            if is_role_account(target):
                return False, f"role_account:{target.split('@', 1)[0]}"
        except Exception as exc:
            return False, f"validator_error:{type(exc).__name__}:{exc}"
        merged = load_merged_suppression()
        if target in merged:
            return False, f"suppressed:{target}"
        domain = target.rsplit("@", 1)[-1]
        if f"@{domain}" in merged:
            return False, f"suppressed_domain:@{domain}"
        recent = last_send_ts(target)
        if recent is not None and (_now() - recent) < timedelta(minutes=RECENT_SEND_CAP_MINUTES):
            return False, f"recent_send_cap_{RECENT_SEND_CAP_MINUTES}m:last_send={recent.isoformat()}"
        if cold:
            if recent is not None and (_now() - recent) < timedelta(days=FREQUENCY_CAP_DAYS):
                return False, f"frequency_cap_{FREQUENCY_CAP_DAYS}d:last_send={recent.isoformat()}"
        return True, "ok"
    except Exception as exc:  # fail CLOSED — a broken gate must never allow a send
        return False, f"gate_error:{type(exc).__name__}:{exc}"


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
