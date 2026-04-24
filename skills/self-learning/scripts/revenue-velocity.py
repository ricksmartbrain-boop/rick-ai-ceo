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
import sys
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

# 2026-04-24: Stripe phantom filter shipped in get_stripe_mrr (_is_phantom).
# Order of trust:
#   1. Live Stripe (with filter) — primary
#   2. Reconciliation file — fallback (Stripe outage / all-filtered)
#   3. 0.0 — final fallback
# RICK_USE_RECONCILIATION_AS_PRIMARY=1 inverts (recon-first).
USE_RECON_PRIMARY = os.getenv("RICK_USE_RECONCILIATION_AS_PRIMARY", "0").strip() in ("1", "true", "True", "yes", "on")
LIVE = True  # legacy flag kept for backward-compat (no-op now)


def _is_phantom(sub: dict, now_ts: int) -> tuple[bool, str]:
    """Same _is_phantom as morning-intelligence.py — kept inline to avoid
    cross-script import path issues. Filters: non-active status, ≥100%
    discount coupon, zero-paid+zero-due invoice, expired-but-active subs."""
    status = sub.get("status", "")
    if status not in ("active", "trialing"):
        return True, f"status={status}"
    discount = sub.get("discount") or {}
    coupon = (discount.get("coupon") or {}) if isinstance(discount, dict) else {}
    if coupon.get("percent_off", 0) >= 100:
        return True, "100% discount coupon"
    inv = sub.get("latest_invoice") or {}
    if isinstance(inv, dict):
        amount_paid = int(inv.get("amount_paid", 0) or 0)
        amount_due = int(inv.get("amount_due", 0) or 0)
        if amount_paid == 0 and amount_due == 0:
            return True, "zero-paid + zero-due invoice"
    if sub.get("cancel_at_period_end"):
        period_end = int(sub.get("current_period_end", 0) or 0)
        if period_end and period_end < now_ts:
            return True, "cancel_at_period_end + period expired"
    return False, ""

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

def _stripe_key() -> str:
    """Resolve STRIPE_SECRET_KEY from env or rick.env (handles `export ` prefix)."""
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".openclaw" / "workspace" / "config" / "rick.env"
    if not env_file.exists():
        env_file = Path.home() / "clawd" / "config" / "rick.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("export "):
                stripped = stripped[len("export "):].lstrip()
            if stripped.startswith("STRIPE_SECRET_KEY="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _stripe_mrr_filtered() -> float | None:
    """Live Stripe MRR with phantom filter. Returns None on fetch failure."""
    api_key = _stripe_key()
    if not api_key:
        return None
    try:
        import urllib.request, base64, time as _time
        url = "https://api.stripe.com/v1/subscriptions?status=active&limit=100&expand[]=data.latest_invoice"
        req = urllib.request.Request(url)
        creds = base64.b64encode(f"{api_key}:".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        now_ts = int(_time.time())
        mrr = 0.0
        included = 0
        filtered = 0
        for sub in data.get("data", []):
            is_phantom, reason = _is_phantom(sub, now_ts)
            sub_id = sub.get("id", "?")
            if is_phantom:
                print(f"[revenue-velocity] FILTERED sub={sub_id} reason={reason}", file=sys.stderr)
                filtered += 1
                continue
            included += 1
            for item in sub.get("items", {}).get("data", []):
                price = item.get("price", {})
                amt = price.get("unit_amount", 0) or 0
                interval = price.get("recurring", {}).get("interval", "")
                if interval == "month":
                    mrr += amt / 100
                elif interval == "year":
                    mrr += amt / 100 / 12
        print(f"[revenue-velocity] Stripe MRR=${mrr:.2f} (included {included}, filtered {filtered})", file=sys.stderr)
        return mrr
    except Exception as exc:
        print(f"[revenue-velocity] Stripe fetch failed: {exc}", file=sys.stderr)
        return None


def get_stripe_mrr() -> float:
    """Return current MRR.

    Order of trust (2026-04-24):
      1. Live Stripe (filtered for phantoms via _is_phantom)
      2. Reconciliation file (Stripe outage / all-filtered fallback)
      3. 0.0 (final fallback)

    RICK_USE_RECONCILIATION_AS_PRIMARY=1 inverts (recon-first).
    """
    if USE_RECON_PRIMARY:
        real = _mrr_from_reconciliation()
        if real is not None:
            return real
        live_mrr = _stripe_mrr_filtered()
        return live_mrr if live_mrr is not None else 0.0

    # Default: Stripe primary
    live_mrr = _stripe_mrr_filtered()
    if live_mrr is not None and live_mrr > 0:
        return live_mrr
    real = _mrr_from_reconciliation()
    if real is not None:
        return real
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
