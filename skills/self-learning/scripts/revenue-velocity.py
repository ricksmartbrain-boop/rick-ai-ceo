#!/usr/bin/env python3
"""
revenue-velocity.py — Track MRR velocity and trigger escalation when flat/declining
Runs every 6 hours. Checks Stripe, computes velocity, fires escalation if needed.

Thresholds (from Opus + GPT-5.4 design):
- Day 7:  must have at least 1 pricing page view → else escalate
- Day 14: must have at least 1 inbound conversation → else escalate
- Day 21: must have at least 1 checkout session → else PIVOT signal
- Day 30: must have first sale → else REINVENT signal
- 5+ consecutive days flat/declining → escalate to Approvals topic
"""

from __future__ import annotations
import json
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
WORKSPACE = Path(os.getenv("RICK_WORKSPACE_ROOT", str(Path.home() / ".openclaw/workspace")))
NOW = datetime.now().isoformat()
TODAY = date.today()

REVENUE_DIR = DATA_ROOT / "revenue"
VELOCITY_LOG = REVENUE_DIR / "velocity.json"

# Day 1 = launch date
LAUNCH_DATE = date(2026, 3, 13)  # meetrick.ai live date

# RICK_REVENUE_VELOCITY_LIVE gates the phantom-prone Stripe sum + escalations.
# Default OFF (dry-run) until the Stripe MRR query is rewritten to filter
# phantom subs (100%-discount Credits Booster etc.). See
# rick-vault/revenue/reconciliation-2026-04-20.md for the canonical $9 MRR.
LIVE = os.getenv("RICK_REVENUE_VELOCITY_LIVE", "0").strip() in ("1", "true", "True", "yes", "on")

def days_since_launch() -> int:
    return (TODAY - LAUNCH_DATE).days

def _mrr_from_reconciliation() -> float | None:
    """Parse the latest reconciliation-YYYY-MM-DD.md file for canonical MRR.

    The reconciliation file is hand-curated truth. Format expected:
      **Real current MRR:** **$X.XX**
    Returns None if no reconciliation file exists or parse fails.
    """
    candidates = sorted(REVENUE_DIR.glob("reconciliation-*.md"), reverse=True)
    if not candidates:
        return None
    text = candidates[0].read_text(encoding="utf-8", errors="replace")
    # Match e.g. "Real current MRR:** **$9.00**" — tolerate spacing/markdown
    m = re.search(r"Real\s+current\s+MRR[^\$]*\$\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (ValueError, TypeError):
        return None

def get_stripe_mrr() -> float:
    """Return current MRR.

    Order of trust:
      1. Latest reconciliation-*.md file (hand-curated truth, filters phantom subs)
      2. If LIVE=1: raw Stripe sum (KNOWN BUGGY — includes 100%-discount phantom subs)
      3. $0 (graceful fallback — never use stale snapshot.json which contains $547)
    """
    real = _mrr_from_reconciliation()
    if real is not None:
        return real

    if not LIVE:
        # Without reconciliation truth + without LIVE flag, refuse to emit a number.
        # Better to report $0 than to re-introduce the phantom $547.
        return 0.0

    # LIVE=1 path: hit Stripe directly. NOTE: this still includes phantom
    # Credits Booster subs (100% discount, $0 actually paid). Fix is to
    # filter by latest_invoice.amount_paid > 0 — TODO before flipping LIVE on.
    import urllib.request
    api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not api_key:
        return 0.0
    try:
        url = "https://api.stripe.com/v1/subscriptions?status=active&limit=100"
        req = urllib.request.Request(url)
        import base64
        creds = base64.b64encode(f"{api_key}:".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        mrr = 0.0
        for sub in data.get("data", []):
            for item in sub.get("items", {}).get("data", []):
                price = item.get("price", {})
                amt = price.get("unit_amount", 0) or 0
                interval = price.get("recurring", {}).get("interval", "")
                if interval == "month":
                    mrr += amt / 100
                elif interval == "year":
                    mrr += amt / 100 / 12
        return mrr
    except Exception:
        return 0.0

def load_velocity_log() -> dict:
    if VELOCITY_LOG.exists():
        try:
            return json.loads(VELOCITY_LOG.read_text())
        except Exception:
            pass
    return {"updated_at": NOW, "entries": [], "consecutive_flat_days": 0, "last_escalation": None}

def save_velocity_log(data: dict) -> None:
    data["updated_at"] = NOW
    REVENUE_DIR.mkdir(parents=True, exist_ok=True)
    VELOCITY_LOG.write_text(json.dumps(data, indent=2))

def send_telegram(topic: str, text: str) -> None:
    try:
        subprocess.run(
            ["bash", str(WORKSPACE / "scripts/tg-topic.sh"), topic, text],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

def trigger_escalation(reason: str, intensity: str, vel: dict) -> None:
    """Fire escalation based on intensity level."""
    day = days_since_launch()
    message = (
        f"⚡ **Revenue Escalation — {intensity}**\n"
        f"Day {day} since launch\n"
        f"Reason: {reason}\n"
        f"Consecutive flat days: {vel.get('consecutive_flat_days',0)}\n\n"
    )

    if intensity == "TWEAK":
        message += "Action: Review and adjust current X content strategy. Check funnel bottleneck."
        send_telegram("ceo-hq", message)
    elif intensity == "SHIFT":
        message += "Action: Change primary conversion CTA. Test new offer angle. Queue experiment immediately."
        send_telegram("approvals", message)
        # Also queue an experiment
        try:
            subprocess.run(
                ["python3", str(WORKSPACE / "skills/self-learning/scripts/experiment-engine.py"), "--generate"],
                timeout=60, capture_output=True
            )
        except Exception:
            pass
    elif intensity == "PIVOT":
        message += "Action: Current strategy is not working. Requires founder input on direction change."
        send_telegram("approvals", message)
    elif intensity == "REINVENT":
        message += "🔴 CRITICAL: 30+ days, $0 revenue. This is a business model problem, not execution. Immediate war room required."
        send_telegram("approvals", message)
        send_telegram("ceo-hq", message)

    vel["last_escalation"] = {"date": TODAY.isoformat(), "reason": reason, "intensity": intensity}
    print(f"[revenue-velocity] Escalation fired: {intensity} — {reason}")

def check_milestone_gates(mrr: float, vel: dict) -> None:
    """Check hard deadlines per Opus circuit breaker design."""
    day = days_since_launch()
    entries = vel.get("entries", [])

    # Already escalated today?
    last_esc = vel.get("last_escalation", {})
    if last_esc and last_esc.get("date") == TODAY.isoformat():
        return

    if day >= 30 and mrr == 0:
        trigger_escalation("Day 30+, $0 MRR — reinvention required", "REINVENT", vel)
    elif day >= 21 and mrr == 0 and vel.get("consecutive_flat_days", 0) >= 7:
        trigger_escalation(f"Day {day}, no checkout sessions in 7 days", "PIVOT", vel)
    elif day >= 14 and mrr == 0 and vel.get("consecutive_flat_days", 0) >= 5:
        trigger_escalation(f"Day {day}, flat revenue 5+ consecutive days", "SHIFT", vel)
    elif day >= 7 and mrr == 0 and vel.get("consecutive_flat_days", 0) >= 3:
        trigger_escalation(f"Day {day}, $0 revenue — strategy check needed", "TWEAK", vel)

def main() -> None:
    mrr = get_stripe_mrr()
    vel = load_velocity_log()

    entries = vel.get("entries", [])
    today_str = TODAY.isoformat()

    # Record today's entry. If today already exists (e.g. an earlier cron wrote
    # phantom $547 before this script was patched), OVERWRITE it with the
    # current canonical reading rather than skipping — this is what makes the
    # patch self-healing instead of waiting until tomorrow.
    if not entries or entries[-1].get("date") != today_str:
        entries.append({"date": today_str, "mrr": mrr})
    else:
        entries[-1]["mrr"] = mrr
    vel["entries"] = entries[-90:]  # keep 90 days

    # Compute velocity
    # Cap flat_days to entries count — prevents accumulation of pre-deployment Stripe history
    if len(entries) >= 2:
        prev_mrr = entries[-2]["mrr"]
        delta = mrr - prev_mrr
        if delta <= 0:
            raw = vel.get("consecutive_flat_days", 0) + 1
            vel["consecutive_flat_days"] = min(raw, len(entries))  # never exceed tracked history
        else:
            vel["consecutive_flat_days"] = 0
    
    # 7d and 30d velocity
    entries_7d = [e for e in entries if e["date"] >= (TODAY - timedelta(days=7)).isoformat()]
    entries_30d = [e for e in entries if e["date"] >= (TODAY - timedelta(days=30)).isoformat()]
    delta_7d = (entries_7d[-1]["mrr"] - entries_7d[0]["mrr"]) if len(entries_7d) >= 2 else 0
    delta_30d = (entries_30d[-1]["mrr"] - entries_30d[0]["mrr"]) if len(entries_30d) >= 2 else 0

    vel["current_mrr"] = mrr
    vel["delta_7d"] = delta_7d
    vel["delta_30d"] = delta_30d

    print(f"[revenue-velocity] MRR=${mrr:.0f} | Δ7d=${delta_7d:+.0f} | Δ30d=${delta_30d:+.0f} | flat_days={vel.get('consecutive_flat_days',0)}")

    check_milestone_gates(mrr, vel)
    save_velocity_log(vel)

    # Write to daily note
    daily = DATA_ROOT / f"memory/{today_str}.md"
    if daily.exists():
        line = f"\n- **Revenue velocity**: MRR=${mrr:.0f} | Δ7d=${delta_7d:+.0f} | flat_days={vel.get('consecutive_flat_days',0)}\n"
        with open(daily, "a") as f:
            f.write(line)

if __name__ == "__main__":
    main()
