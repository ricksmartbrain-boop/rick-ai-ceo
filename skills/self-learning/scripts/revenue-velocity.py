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
from datetime import date, datetime, timedelta, timezone
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

# 2026-07-13: product-ID allowlist. The heuristic filter only catches the
# Folderly $269s while they stay 100%-comped; the allowlist is structural.
# Attribution-truth v2: current_mrr = RICK_PRODUCT_IDS only (meetrick);
# LinguaLive is Khrystyna's — tracked separately as portfolio_mrr, NEVER summed.
try:
    sys.path.insert(0, str(WORKSPACE))
    from runtime.revenue_signals import RICK_PRODUCT_IDS, PORTFOLIO_PRODUCT_IDS
except Exception:
    # Inline fallback — this script avoids hard cross-imports (see _is_phantom
    # note). Keep in sync with runtime/revenue_signals.py.
    RICK_PRODUCT_IDS = frozenset({
        "prod_UAnyfcxSShF33a", "prod_UAbJqha8GV2lDC", "prod_URTpF3D8hAHUmX",
        "prod_URTpx3MtWC0WYB", "prod_URTptzJL9gfGAT", "prod_U8mgNHBNVO9Zsy",
        "prod_U8mgdTxX1zzryq",
    })
    PORTFOLIO_PRODUCT_IDS = frozenset({"prod_TV7oz6jtR1ejfd"})


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


def _stripe_mrr_filtered() -> tuple[float, float] | None:
    """Live Stripe MRR with phantom filter. Returns (rick_mrr, portfolio_mrr)
    or None on fetch failure. rick_mrr = RICK_PRODUCT_IDS (meetrick) only;
    portfolio_mrr = PORTFOLIO_PRODUCT_IDS (LinguaLive — not Rick's). Never sum."""
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
        rick_mrr = 0.0
        portfolio_mrr = 0.0
        included = 0
        filtered = 0
        for sub in data.get("data", []):
            is_phantom, reason = _is_phantom(sub, now_ts)
            sub_id = sub.get("id", "?")
            if is_phantom:
                print(f"[revenue-velocity] FILTERED sub={sub_id} reason={reason}", file=sys.stderr)
                filtered += 1
                continue
            sub_products = {
                (item.get("price") or {}).get("product")
                for item in sub.get("items", {}).get("data", [])
            }
            if not (sub_products & (RICK_PRODUCT_IDS | PORTFOLIO_PRODUCT_IDS)):
                print(f"[revenue-velocity] FILTERED sub={sub_id} reason=product not in RICK/PORTFOLIO allowlists", file=sys.stderr)
                filtered += 1
                continue
            included += 1
            for item in sub.get("items", {}).get("data", []):
                price = item.get("price", {})
                product = price.get("product")
                amt = price.get("unit_amount", 0) or 0
                interval = price.get("recurring", {}).get("interval", "")
                item_mrr = 0.0
                if interval == "month":
                    item_mrr = amt / 100
                elif interval == "year":
                    item_mrr = amt / 100 / 12
                if product in RICK_PRODUCT_IDS:
                    rick_mrr += item_mrr
                elif product in PORTFOLIO_PRODUCT_IDS:
                    portfolio_mrr += item_mrr
        print(f"[revenue-velocity] Stripe Rick MRR=${rick_mrr:.2f} | portfolio (LinguaLive, not Rick's)=${portfolio_mrr:.2f} (included {included}, filtered {filtered})", file=sys.stderr)
        return rick_mrr, portfolio_mrr
    except Exception as exc:
        print(f"[revenue-velocity] Stripe fetch failed: {exc}", file=sys.stderr)
        return None


def _consistency_check(live_mrr: float | None) -> None:
    """Cross-source truth check (2026-07-12): if live Stripe and a fresh
    (<24h old) reconciliation file disagree by >10%, append one line to
    operations/log-anomalies.md. Silent no-op when either source is
    missing, zero, or the reconciliation file is stale."""
    if live_mrr is None or live_mrr <= 0:
        return
    candidates = sorted(REVENUE_DIR.glob("reconciliation-*.md"), reverse=True)
    if not candidates:
        return
    recon_path = candidates[0]
    try:
        age_s = datetime.now().timestamp() - recon_path.stat().st_mtime
    except OSError:
        return
    if age_s > 24 * 3600:
        return
    recon_mrr = _mrr_from_reconciliation()
    if recon_mrr is None or recon_mrr <= 0:
        return
    if abs(live_mrr - recon_mrr) / max(live_mrr, recon_mrr) > 0.10:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = (
            f"- {ts} [revenue-split-brain] live Stripe MRR ${live_mrr:.2f} vs "
            f"{recon_path.name} ${recon_mrr:.2f} disagree >10% within 24h — "
            f"reconcile revenue sources (revenue-velocity consistency check)\n"
        )
        anomalies = DATA_ROOT / "operations" / "log-anomalies.md"
        try:
            anomalies.parent.mkdir(parents=True, exist_ok=True)
            with anomalies.open("a", encoding="utf-8") as f:
                f.write(line)
            print(f"[revenue-velocity] ANOMALY logged: live ${live_mrr:.2f} vs recon ${recon_mrr:.2f}", file=sys.stderr)
        except OSError as exc:
            print(f"[revenue-velocity] anomaly log write failed: {exc}", file=sys.stderr)


def get_stripe_mrr() -> tuple[float, float]:
    """Return (rick_mrr, portfolio_mrr). rick_mrr = meetrick products only;
    portfolio_mrr = LinguaLive ops (Khrystyna's, NOT Rick's revenue).

    Order of trust (2026-04-24):
      1. Live Stripe (filtered for phantoms via _is_phantom)
      2. Reconciliation file (Stripe outage / all-filtered fallback; Rick-only,
         portfolio falls back to 0.0)
      3. 0.0 (final fallback)

    RICK_USE_RECONCILIATION_AS_PRIMARY=1 inverts (recon-first).
    """
    if USE_RECON_PRIMARY:
        real = _mrr_from_reconciliation()
        if real is not None:
            return real, 0.0
        live = _stripe_mrr_filtered()
        return live if live is not None else (0.0, 0.0)

    # Default: Stripe primary
    live = _stripe_mrr_filtered()
    rick_live = live[0] if live is not None else None
    _consistency_check(rick_live)
    if rick_live is not None and rick_live > 0:
        return live
    real = _mrr_from_reconciliation()
    if real is not None:
        return real, (live[1] if live is not None else 0.0)
    return (0.0, live[1] if live is not None else 0.0)

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

# topic→(chat_id, thread_id) — migrated from tg-topic.sh (Strategy-C #1)
_TG_TOPIC_MAP = {
    "ops-alerts": ("-1003781085932", 34), "ops": ("-1003781085932", 34),
    "approvals":  ("-1003781085932", 26), "customer":  ("-1003781085932", 32),
    "product-lab":("-1003781085932", 28), "distribution":("-1003781085932", 30),
    "traffic":    ("-1003781085932", 715), "test":      ("-1003781085932", 36),
    "ceo-hq":     ("-1003781085932", 24),
}


def send_telegram(topic: str, text: str) -> None:
    """Send to named Telegram topic via openclaw message send (tg-topic.sh fallback)."""
    entry = _TG_TOPIC_MAP.get(topic)
    if entry:
        chat_id, tid = entry
        try:
            r = subprocess.run(
                [
                    "openclaw", "message", "send",
                    "--channel", "telegram",
                    "--target", chat_id,
                    "--thread-id", str(tid),
                    "--message", text,
                ],
                capture_output=True, timeout=15,
            )
            if r.returncode == 0:
                return
        except Exception:
            pass
    # Fallback: tg-topic.sh
    try:
        subprocess.run(
            ["bash", str(WORKSPACE / "scripts/tg-topic.sh"), topic, text],
            capture_output=True, timeout=10,
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
    mrr, portfolio_mrr = get_stripe_mrr()
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

    vel["current_mrr"] = mrr  # Rick-only (meetrick products) — attribution-truth v2
    vel["portfolio_mrr"] = portfolio_mrr  # LinguaLive ops (Khrystyna's) — NOT Rick's, never sum
    vel["delta_7d"] = delta_7d
    vel["delta_30d"] = delta_30d

    print(f"[revenue-velocity] Rick MRR=${mrr:.0f} | portfolio=${portfolio_mrr:.0f} (not Rick's) | Δ7d=${delta_7d:+.0f} | Δ30d=${delta_30d:+.0f} | flat_days={vel.get('consecutive_flat_days',0)}")

    check_milestone_gates(mrr, vel)
    save_velocity_log(vel)

    # Write to daily note
    daily = DATA_ROOT / f"memory/{today_str}.md"
    if daily.exists():
        line = f"\n- **Revenue velocity**: Rick MRR=${mrr:.0f} (meetrick only) | portfolio=${portfolio_mrr:.0f} (LinguaLive, not Rick's) | Δ7d=${delta_7d:+.0f} | flat_days={vel.get('consecutive_flat_days',0)}\n"
        with open(daily, "a") as f:
            f.write(line)

if __name__ == "__main__":
    main()
