"""Per-flag staleness probes for LIVE env flags.

Reads the most recent successful activity timestamp from each flag's known
log file. Surfaces staleness in the daily digest so silent regressions
(flag ON but loop dead) get caught within the staleness window.

This is observation-only: never flips flags, never restarts services. It
exists to surface the "X silent for 4 days" class of bug that previously
required Vlad to diagnose by hand.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"

# (flag_name, log_relative_path, max_age_hours, optional_filter_lambda)
# max_age_hours: how stale before the flag is "stale" (red in digest)
# filter_lambda(entry_dict) → bool: True if entry counts as a successful run.
#   Default (None) treats any entry as success.
FLAG_PROBES: list[tuple[str, str, float, Optional[Callable[[dict], bool]]]] = [
    # FENIX filter: only count live-mode decisions. Without this, observe-mode
    # entries (written when RICK_FENIX_LIVE=0) would mask a silently-broken
    # live-mode pipeline because someone else keeps writing to the same log.
    ("RICK_FENIX_LIVE", "fenix-decisions.jsonl", 26.0,
        lambda e: e.get("mode") == "live"),
    ("RICK_HIVE_ENABLED", "hive-heartbeat.jsonl", 1.5,
        lambda e: e.get("result") == "posted" or e.get("status") == "ok"),
    # 26h not 8h: hive-sync's actual cadence is daily (04:30 PT per
    # ai.rick.hive-sync.plist), not every 6h as I originally assumed. 26h
    # gives 2h slack on a 24h cron. Confirmed via Rick TUI 2026-04-25.
    ("RICK_HIVE_SYNC_LIVE", "hive-sync.jsonl", 26.0,
        lambda e: e.get("status") == "ok"),
    ("RICK_VARA_LIVE", "vara.jsonl", 168.0, None),
    # 170h not 26h: lead-replay runs weekly (Mondays 09:00 PT per
    # ai.rick.lead-replay.plist StartCalendarInterval). 26h flapped between
    # cycles. 170h gives 2h slack on the 168h cadence.
    ("RICK_LEAD_REPLAY_LIVE", "lead-replay.jsonl", 170.0, None),
    ("RICK_IMAP_LIVE", "imap-watcher.jsonl", 1.0,
        lambda e: e.get("status") == "ok"),
    ("RICK_REPLY_ROUTER_LIVE", "reply-router.jsonl", 26.0, None),
    ("RICK_OUTBOUND_MOLTBOOK_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "moltbook"
        and e.get("status") in ("sent", "observed-only")),
    # TODO(2026-04-25): RICK_EMAIL_SEND_LIVE probe pulled — Rick TUI
    # confirmed email sends bypass outbound-dispatcher.jsonl and resolve
    # via the Resend API directly (verified Apr 25: 5 deliveries in last
    # 4h despite this log showing 95h stale). Re-add once we identify the
    # right log surface — likely a Resend webhook log or
    # skills/email-automation/scripts/email-sequence-dispatch.py output.
    ("RICK_OUTBOUND_THREADS_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "threads"
        and e.get("status") in ("sent", "observed-only")),
    ("RICK_OUTBOUND_INSTAGRAM_LIVE", "outbound-dispatcher.jsonl", 26.0,
        lambda e: e.get("channel") == "instagram"
        and e.get("status") in ("sent", "observed-only")),
    ("RICK_ANALYTICS_LIVE", "analytics-ingest.jsonl", 26.0, None),
]


def _parse_ts(entry: dict) -> Optional[datetime]:
    raw = entry.get("ts") or entry.get("ran_at") or entry.get("timestamp")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _iter_jsonl_reverse(path: Path, max_lines: int = 5000):
    """Yield parsed JSON entries from end of file. Bounded for big logs.

    max_lines caps memory at ~5K * line_size; sufficient for most flags
    since we stop at the first matching entry.
    """
    if not path.exists():
        return
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    lines = text.splitlines()
    for line in reversed(lines[-max_lines:]):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _flag_is_on(flag: str) -> bool:
    val = os.getenv(flag, "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def scan_flags() -> list[dict]:
    """Return per-flag status records.

    Each record: {flag, on, last_success_ts, age_hours, status,
                  log_path, max_age_hours}
    status ∈ {"fresh", "stale", "no_data", "off"}.
    """
    results = []
    now = datetime.now()
    for flag, rel_path, max_age_h, filt in FLAG_PROBES:
        log_path = OPS / rel_path
        on = _flag_is_on(flag)
        record = {
            "flag": flag,
            "on": on,
            "last_success_ts": None,
            "age_hours": None,
            "status": "off" if not on else "no_data",
            "log_path": str(log_path),
            "max_age_hours": max_age_h,
        }
        if not on:
            results.append(record)
            continue
        latest = None
        for entry in _iter_jsonl_reverse(log_path):
            if filt is not None:
                try:
                    if not filt(entry):
                        continue
                except Exception:
                    continue
            ts = _parse_ts(entry)
            if ts is not None:
                latest = ts
                break
        if latest is None:
            results.append(record)
            continue
        # Strip tz on the parsed ts so subtraction with naive `now` doesn't
        # raise when a logger writes `2026-04-25T...+00:00`. Time math is
        # local-clock for both sides; logs are wall-clock anyway.
        if latest.tzinfo is not None:
            latest = latest.replace(tzinfo=None)
        try:
            age_h = (now - latest).total_seconds() / 3600.0
        except TypeError:
            results.append(record)
            continue
        record["last_success_ts"] = latest.isoformat(timespec="seconds")
        record["age_hours"] = round(age_h, 2)
        record["status"] = "fresh" if age_h <= max_age_h else "stale"
        results.append(record)
    return results


def stale_flags() -> list[dict]:
    return [r for r in scan_flags() if r["status"] == "stale"]


if __name__ == "__main__":
    import sys
    for r in scan_flags():
        flag, status, age = r["flag"], r["status"], r["age_hours"]
        age_str = f"{age:.1f}h" if age is not None else "-"
        sys.stdout.write(f"{status:>8}  {flag:<32} {age_str}\n")
