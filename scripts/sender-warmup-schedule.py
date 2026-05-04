#!/usr/bin/env python3
"""sender-warmup-schedule.py — 14-day sender reputation warmup plan.

Usage:
  python3 scripts/sender-warmup-schedule.py            # Print full schedule + today's cap
  python3 scripts/sender-warmup-schedule.py --status   # Current day + cap only (for crons)
  python3 scripts/sender-warmup-schedule.py --digest   # One-line digest for heartbeat injection
  python3 scripts/sender-warmup-schedule.py --init     # Set warmup_started_at = now in state file
  python3 scripts/sender-warmup-schedule.py --check    # Exit 0 if sends remaining, 1 if at cap

State file: $RICK_DATA_ROOT/control/sender-warmup-state.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"
STATE_FILE = DATA_ROOT / "control" / "sender-warmup-state.json"
# email-sends.jsonl may live under rick-vault/operations when RICK_DATA_ROOT
# points to a different root (e.g. rick-install-test/data). Search both.
_SENDS_CANDIDATES = [
    OPS / "email-sends.jsonl",
    Path.home() / "rick-vault" / "operations" / "email-sends.jsonl",
]
SENDS_FILE = next((p for p in _SENDS_CANDIDATES if p.exists()), _SENDS_CANDIDATES[0])
SEQUENCE_SENDS_FILE = OPS / "email-sequence-send.jsonl"

# ---------------------------------------------------------------------------
# Warmup ramp schedule — day → max sends/day (cold outreach only)
# ---------------------------------------------------------------------------
RAMP: list[tuple[int, int]] = [
    (1,  5),
    (4,  10),
    (7,  20),
    (10, 35),
    (14, 50),
]

def cap_for_day(day_number: int) -> int:
    """Return the send cap for the given 1-based day number."""
    cap = RAMP[0][1]
    for day_thresh, limit in RAMP:
        if day_number >= day_thresh:
            cap = limit
    return cap


def get_today_cap(state: dict | None = None) -> int:
    """Return today's cap from the warmup state file (Day 1 = 5 if unstarted)."""
    state = state if isinstance(state, dict) else _load_state()
    day_num = current_day_number(state)
    return cap_for_day(day_num) if day_num > 0 else RAMP[0][1]

def full_schedule() -> list[dict]:
    """Return per-day rows for the full 14-day plan."""
    rows = []
    for d in range(1, 15):
        rows.append({
            "day": d,
            "cap": cap_for_day(d),
            "milestone": _milestone(d),
        })
    return rows

def _milestone(day: int) -> str:
    milestones = {
        1: "🔴 Recovery start — 5/day max",
        4: "🟡 Step up — 10/day",
        7: "🟢 Halfway — 20/day",
        10: "🔵 Scaling — 35/day",
        14: "✅ Full warmup — 50/day cap",
    }
    return milestones.get(day, "")

# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {}

def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))

def _warmup_started_at(state: dict) -> datetime | None:
    ts = state.get("warmup_started_at")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

def current_day_number(state: dict) -> int:
    started = _warmup_started_at(state)
    if not started:
        return 0  # warmup not started
    delta = _now_utc() - started
    return max(1, delta.days + 1)

# ---------------------------------------------------------------------------
# Sends-today counter (reads email-sends.jsonl)
# ---------------------------------------------------------------------------

def sends_today() -> int:
    today = _now_utc().date().isoformat()
    count = 0
    for path in (SENDS_FILE, SEQUENCE_SENDS_FILE):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                ts = (r.get("ts") or r.get("timestamp") or "")[:10]
                if ts == today and r.get("status") == "sent":
                    count += 1
            except (json.JSONDecodeError, TypeError):
                pass
    return count

# ---------------------------------------------------------------------------
# Bounce rate (7d from local JSONL)
# ---------------------------------------------------------------------------

def sender_rep_7d() -> dict:
    bounces_file = OPS / "email-bounces.jsonl"
    cutoff = _now_utc() - timedelta(days=7)
    bounces = 0
    complaints = 0
    sends = 0

    for path in (SENDS_FILE, SEQUENCE_SENDS_FILE):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            try:
                r = json.loads(line)
                ts_raw = r.get("ts") or r.get("timestamp") or ""
                if not ts_raw:
                    continue
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff and r.get("status") == "sent":
                    sends += 1
            except (json.JSONDecodeError, ValueError):
                pass

    if bounces_file.exists():
        for line in bounces_file.read_text().splitlines():
            try:
                r = json.loads(line)
                ts_raw = r.get("ts", "")
                if not ts_raw:
                    continue
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    continue
                if r.get("event") == "bounced":
                    bounces += 1
                elif r.get("event") == "complained":
                    complaints += 1
            except (json.JSONDecodeError, ValueError):
                pass

    bounce_rate = round(bounces / max(sends, 1) * 100, 1)
    complaint_rate = round(complaints / max(sends, 1) * 100, 1)
    delivery_rate = round(100 - bounce_rate, 1)
    return {
        "sends_7d": sends,
        "bounces_7d": bounces,
        "complaints_7d": complaints,
        "bounce_rate_pct": bounce_rate,
        "complaint_rate_pct": complaint_rate,
        "delivery_rate_pct": delivery_rate,
    }

# ---------------------------------------------------------------------------
# Rep health status
# ---------------------------------------------------------------------------

def rep_health(bounce_rate: float) -> str:
    if bounce_rate < 2.0:
        return "✅ HEALTHY"
    if bounce_rate < 5.0:
        return "⚠️ WARNING"
    if bounce_rate < 10.0:
        return "🔴 CRITICAL"
    return "🚨 EMERGENCY — pause sends immediately"

# ---------------------------------------------------------------------------
# Resend warmup-mode recommendation (not auto-applied per constraints)
# ---------------------------------------------------------------------------

RESEND_WARMUP_RECOMMENDATION = """
⚠️  RESEND WARMUP-MODE RECOMMENDATION (action required by Vlad):
    1. Log into https://resend.com/domains
    2. Select meetrick.ai
    3. Enable "IP Warmup" if available on your plan
    4. Set max daily limit matching the schedule below
    Note: Resend does not expose a warmup-mode toggle via API (as of May 2026).
    Manual UI action required. Rick will NOT auto-toggle this.
""".strip()

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sender warmup schedule manager")
    parser.add_argument("--status", action="store_true", help="Current day + cap")
    parser.add_argument("--digest", action="store_true", help="One-line digest for heartbeat")
    parser.add_argument("--init", action="store_true", help="Initialize warmup start date to now")
    parser.add_argument("--check", action="store_true", help="Exit 0 if sends remain, 1 if at cap")
    parser.add_argument("--json", action="store_true", help="Output status as JSON")
    args = parser.parse_args()

    state = _load_state()
    rep = sender_rep_7d()
    today_sent = sends_today()

    if args.init:
        state["warmup_started_at"] = _now_utc().isoformat()
        state["initialized_by"] = "sender-warmup-schedule.py --init"
        _save_state(state)
        print(f"✅ Warmup started at {state['warmup_started_at']}")
        print(f"   State file: {STATE_FILE}")
        return

    day_num = current_day_number(state)
    started = _warmup_started_at(state)

    if day_num == 0:
        # Warmup not started yet — compute cap as if Day 1
        today_cap = RAMP[0][1]
        day_display = "not started (run --init to begin)"
    else:
        today_cap = cap_for_day(day_num)
        day_display = str(day_num)

    remaining = max(0, today_cap - today_sent)
    health = rep_health(rep["bounce_rate_pct"])

    if args.check:
        sys.exit(0 if remaining > 0 else 1)

    if args.digest:
        print(
            f"📨 Sender rep 7d: bounce_rate={rep['bounce_rate_pct']}%, "
            f"delivery_rate={rep['delivery_rate_pct']}%, "
            f"today's ramp cap={today_cap} sends "
            f"({today_sent} sent, {remaining} remaining) | {health}"
        )
        return

    if args.json:
        out = {
            "day_number": day_num,
            "today_cap": today_cap,
            "today_sent": today_sent,
            "remaining": remaining,
            "warmup_started_at": started.isoformat() if started else None,
            "rep": rep,
            "health": health,
        }
        print(json.dumps(out, indent=2))
        return

    if args.status:
        print(f"Warmup day:     {day_display}")
        print(f"Today cap:      {today_cap}")
        print(f"Sent today:     {today_sent}")
        print(f"Remaining:      {remaining}")
        print(f"7d bounce_rate: {rep['bounce_rate_pct']}%")
        print(f"7d delivery:    {rep['delivery_rate_pct']}%")
        print(f"Rep health:     {health}")
        return

    # Full output
    print("=" * 60)
    print("  SENDER REPUTATION AUDIT — rick@meetrick.ai")
    print("=" * 60)
    print(f"\n  Domain:           meetrick.ai (Resend verified)")
    print(f"  7d sends:         {rep['sends_7d']}")
    print(f"  7d bounces:       {rep['bounces_7d']}")
    print(f"  7d bounce_rate:   {rep['bounce_rate_pct']}%  {health}")
    print(f"  7d delivery_rate: {rep['delivery_rate_pct']}%")
    print(f"  7d complaints:    {rep['complaints_7d']}")
    print(f"  Suppressed total: see email-bounces.jsonl (poll.done events)")

    print(f"\n  Warmup state:     Day {day_display}")
    print(f"  Today's cap:      {today_cap} sends")
    print(f"  Sent today:       {today_sent}")
    print(f"  Remaining today:  {remaining}")

    print("\n" + "-" * 60)
    print("  14-DAY WARMUP RAMP SCHEDULE")
    print("-" * 60)
    print(f"  {'Day':>4}  {'Cap':>6}  {'Note'}")
    for row in full_schedule():
        marker = "◀ TODAY" if row["day"] == day_num else ""
        print(f"  {row['day']:>4}  {row['cap']:>6}  {row['milestone'] or ''} {marker}")

    print("\n" + "-" * 60)
    print("  THROTTLING RULES (applied to campaign-engine.py + drip-sender.py)")
    print("-" * 60)
    print(f"  • Read today's cap from: {STATE_FILE}")
    print(f"  • Check --check flag before each send burst")
    print(f"  • If at cap: queue send to tomorrow, do not exceed")
    print(f"  • Bounce rate > 5%: pause all cold sends, alert Vlad")
    print(f"  • Bounce rate > 10%: FULL STOP + escalate to Vlad immediately")
    print(f"  • Suppressed addresses: never retry (bounce-rate-guardian.py enforces)")

    print(f"\n{RESEND_WARMUP_RECOMMENDATION}")

    print(f"\n  Secondary domain plan: see ~/.openclaw/workspace/SENDER-WARMUP.md")
    print(f"  Digest preview:")
    digest_line = (
        f"  📨 Sender rep 7d: bounce_rate={rep['bounce_rate_pct']}%, "
        f"delivery_rate={rep['delivery_rate_pct']}%, "
        f"today's ramp cap={today_cap} sends "
        f"({today_sent} sent, {remaining} remaining) | {health}"
    )
    print(digest_line)


if __name__ == "__main__":
    main()
