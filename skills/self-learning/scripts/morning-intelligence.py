#!/usr/bin/env python3
"""
morning-intelligence.py — Rick's daily signal aggregator
Runs at 6:00 AM. Replaces empty heartbeats with a real signal briefing.
Reads: Stripe, GA4, X signal tracker, email, experiments
Writes: ~/rick-vault/learning/patterns/morning-brief-YYYY-MM-DD.md
Sends: Telegram CEO HQ topic
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
TODAY = date.today().isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()

# RICK_REVENUE_VELOCITY_LIVE gates the phantom-prone Stripe sum.
# Default OFF until the Stripe MRR query filters phantom subs (100%-discount
# Credits Booster). Canonical truth lives in
# rick-vault/revenue/reconciliation-*.md.
LIVE = os.getenv("RICK_REVENUE_VELOCITY_LIVE", "0").strip() in ("1", "true", "True", "yes", "on")

# ── Stripe snapshot ───────────────────────────────────────────────────────────
def _mrr_from_reconciliation() -> tuple[float, int] | None:
    """Parse the latest reconciliation-YYYY-MM-DD.md for canonical MRR + customer count.

    Reconciliation files are hand-curated truth (filters phantom subs).
    Returns (mrr, customer_count) or None if no reconciliation file / parse fails.
    """
    rev_dir = DATA_ROOT / "revenue"
    candidates = sorted(rev_dir.glob("reconciliation-*.md"), reverse=True)
    if not candidates:
        return None
    text = candidates[0].read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Real\s+current\s+MRR[^\$]*\$\s*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        mrr = float(m.group(1))
    except (ValueError, TypeError):
        return None
    # Count "REAL MRR" subs in the Real recurring revenue section.
    customers = len(re.findall(r"Classification:\s*\*\*REAL\s+MRR\*\*", text, re.IGNORECASE))
    return mrr, max(customers, 0)


def get_stripe_snapshot() -> dict:
    """Return MRR snapshot.

    Order of trust:
      1. Latest reconciliation-*.md file (canonical hand-curated truth)
      2. If LIVE=1: raw Stripe sum (KNOWN BUGGY — includes phantom $269 subs)
      3. None / 0 (graceful fallback — refuse to emit phantom $547)
    """
    real = _mrr_from_reconciliation()
    if real is not None:
        mrr, customers = real
        # Persist daily snapshot so downstream velocity readers see canonical $9.
        snap_dir = DATA_ROOT / "revenue"
        snap_dir.mkdir(parents=True, exist_ok=True)
        today_snap = snap_dir / f"daily-{TODAY}.json"
        today_snap.write_text(json.dumps({
            "mrr": mrr,
            "total_customers": customers,
            "date": TODAY,
            "source": "reconciliation",
        }))
        return {"mrr": mrr, "total_customers": customers, "new_today": 0}

    if not LIVE:
        # Without reconciliation + without LIVE, refuse to emit. Don't write
        # daily-*.json (so we don't poison the velocity log with $547 again).
        print("[morning-intelligence] No reconciliation file and RICK_REVENUE_VELOCITY_LIVE=0; skipping snapshot.", file=sys.stderr)
        return {"mrr": None, "total_customers": 0, "new_today": 0, "error": "no_reconciliation_and_live_flag_off"}

    # LIVE=1 path — raw Stripe (still buggy). Kept for parity until rewritten.
    stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not stripe_key:
        env_file = WORKSPACE / "config" / "rick.env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("STRIPE_SECRET_KEY="):
                    stripe_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not stripe_key:
        print("[morning-intelligence] ERROR: STRIPE_SECRET_KEY not found — MRR will be wrong", file=sys.stderr)
        return {"mrr": None, "total_customers": 0, "new_today": 0, "error": "missing_key"}
    try:
        import urllib.request, base64
        creds = base64.b64encode(f"{stripe_key}:".encode()).decode()
        req = urllib.request.Request(
            "https://api.stripe.com/v1/subscriptions?status=active&limit=100",
            headers={"Authorization": f"Basic {creds}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        subs = data.get("data", [])
        mrr = sum(
            s.get("plan", {}).get("amount", 0) * s.get("quantity", 1)
            for s in subs
        ) / 100
        customer_ids = set(s.get("customer") for s in subs)
        snap_dir = DATA_ROOT / "revenue"
        snap_dir.mkdir(parents=True, exist_ok=True)
        today_snap = snap_dir / f"daily-{TODAY}.json"
        today_snap.write_text(json.dumps({
            "mrr": mrr,
            "total_customers": len(customer_ids),
            "date": TODAY,
            "source": "stripe_raw_unfiltered",
        }))
        return {"mrr": mrr, "total_customers": len(customer_ids), "new_today": 0}
    except Exception as e:
        print(f"[morning-intelligence] ERROR: Stripe API call failed: {e}", file=sys.stderr)
        # Refuse to fall back to snapshot.json (still contains stale $547).
        return {"mrr": None, "total_customers": 0, "new_today": 0, "error": str(e)}

# ── X signal summary ─────────────────────────────────────────────────────────
def get_x_signals() -> dict:
    tracker = DATA_ROOT / "projects/x-twitter/signal-tracker.json"
    if not tracker.exists():
        return {"followers": 0, "posts_7d": 0, "best_type": "unknown", "engagement_rate_7d": 0}
    try:
        data = json.loads(tracker.read_text())
        posts = data.get("posts", [])
        recent = [p for p in posts if p.get("posted_at", "")[:10] >= YESTERDAY]
        rollups = data.get("weekly_rollups", [])
        latest_rollup = rollups[-1] if rollups else {}
        queue_bias = latest_rollup.get("queue_bias", {})
        return {
            "followers": posts[-1]["followers_before"] if posts else 0,
            "posts_7d": len([p for p in posts if p.get("posted_at","")[:10] >= (date.today()-timedelta(days=7)).isoformat()]),
            "best_type": queue_bias.get("winner_type", "unknown"),
            "recent_posts": len(recent),
        }
    except Exception:
        return {}

# ── Experiment status ─────────────────────────────────────────────────────────
def get_experiment_status() -> dict:
    q = DATA_ROOT / "experiments/queue.json"
    if not q.exists():
        return {"active": 0, "succeeded_7d": 0, "failed_7d": 0, "queued": 0}
    try:
        data = json.loads(q.read_text())
        items = data.get("items", [])
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        return {
            "active": len([i for i in items if i.get("status") in ("launched","measuring")]),
            "queued": len([i for i in items if i.get("status") == "queued"]),
            "succeeded_7d": len([i for i in items if i.get("status") == "succeeded" and i.get("launched_at","")[:10] >= cutoff]),
            "failed_7d": len([i for i in items if i.get("status") in ("failed","killed") and i.get("launched_at","")[:10] >= cutoff]),
        }
    except Exception:
        return {}

# ── Revenue velocity ──────────────────────────────────────────────────────────
def get_revenue_velocity() -> dict:
    """Read last 7 Stripe daily snapshots to compute velocity."""
    rev_dir = DATA_ROOT / "revenue"
    snapshots = []
    for i in range(7):
        d = (date.today() - timedelta(days=i)).isoformat()
        f = rev_dir / f"daily-{d}.json"
        if f.exists():
            try:
                s = json.loads(f.read_text())
                snapshots.append(s.get("mrr", 0))
            except Exception:
                pass
    if len(snapshots) >= 2:
        delta_7d = snapshots[0] - snapshots[-1]
        return {"delta_7d_usd": delta_7d, "trend": "up" if delta_7d > 0 else ("flat" if delta_7d == 0 else "down")}
    return {"delta_7d_usd": 0, "trend": "unknown"}

# ── Circuit breaker check ─────────────────────────────────────────────────────
def check_circuit_breakers(stripe: dict, x: dict, velocity: dict) -> list[str]:
    breakers = []
    mrr = stripe.get("mrr", 0)
    days_live = 4  # TODO: read from launch date config
    if days_live >= 21 and stripe.get("checkout_sessions_ever", 0) == 0:
        breakers.append("🔴 BROKEN: 0 checkout sessions after 21 days")
    if mrr == 0 and days_live >= 30:
        breakers.append("🔴 BROKEN: $0 MRR after 30 days — strategy reinvention required")
    if velocity.get("trend") == "down":
        breakers.append("⚠️ VELOCITY: MRR trending down over 7 days")
    return breakers

# ── Build briefing ────────────────────────────────────────────────────────────
def build_brief(stripe: dict, x: dict, experiments: dict, velocity: dict) -> str:
    breakers = check_circuit_breakers(stripe, x, velocity)
    lines = [
        f"# 🧠 Morning Intelligence — {TODAY}",
        "",
        "## Revenue",
        "- MRR: " + (f"${stripe['mrr']:,.0f}" if stripe.get('mrr') is not None else "⚠️ FETCH ERROR"),
        f"- Customers: {stripe.get('total_customers', 0)}",
        f"- New today: {stripe.get('new_today', 0)}",
        f"- 7d velocity: {velocity.get('trend','unknown')} (Δ${velocity.get('delta_7d_usd',0):+,.0f})",
        "",
        "## X / Distribution",
        f"- Followers: {x.get('followers', '?')}",
        f"- Posts last 7d: {x.get('posts_7d', '?')}",
        f"- Best content type: {x.get('best_type','unknown')}",
        "",
        "## Experiments",
        f"- Active: {experiments.get('active',0)} | Queued: {experiments.get('queued',0)}",
        f"- Won last 7d: {experiments.get('succeeded_7d',0)} | Failed: {experiments.get('failed_7d',0)}",
        "",
    ]
    if breakers:
        lines += ["## ⚡ Circuit Breakers TRIGGERED", ""] + [f"- {b}" for b in breakers] + [""]
    else:
        lines += ["## ✅ Circuit Breakers: All clear", ""]

    # Daily 7 questions (from Opus architecture)
    lines += [
        "## Today's 7 Questions",
        "1. What is today's single highest-leverage action toward first paid customer?",
        "2. Which experiment is measuring right now — what does early data say?",
        "3. What content type drove the most followers this week?",
        "4. Is the conversion funnel bottleneck at traffic, capture, or conversion?",
        "5. What's the one thing blocking first revenue that Rick can remove today?",
        "6. What did yesterday's retro say to change — has it changed?",
        "7. Is there a founder conversation worth starting on X today?",
        "",
        "_Answer these in today's daily note. If you can't answer #1 and #5, fix that first._",
    ]
    return "\n".join(lines)

# ── Send to Telegram Ops Alerts ──────────────────────────────────────────────
def send_telegram(text: str) -> None:
    """Route morning brief to Ops Alerts (topic:34), NOT CEO HQ (topic:24)."""
    try:
        subprocess.run(
            ["bash", str(WORKSPACE / "scripts/tg-topic.sh"), "ops-alerts", text],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    stripe = get_stripe_snapshot()
    x = get_x_signals()
    experiments = get_experiment_status()
    velocity = get_revenue_velocity()

    brief = build_brief(stripe, x, experiments, velocity)

    out_dir = DATA_ROOT / "learning/patterns"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"morning-brief-{TODAY}.md"
    out_path.write_text(brief)
    print(f"[morning-intelligence] Brief written: {out_path}")

    # Also write to today's daily note
    daily = DATA_ROOT / f"memory/{TODAY}.md"
    if daily.exists():
        existing = daily.read_text()
        if "## Morning Intelligence" not in existing:
            with open(daily, "a") as f:
                f.write(f"\n\n## Morning Intelligence\n\n{brief}\n")

    # Telegram summary (short version)
    mrr = stripe.get("mrr")
    mrr_str = f"${mrr:,.0f}" if mrr is not None else "⚠️ FETCH ERROR"
    followers = x.get("followers", "?")
    vel = velocity.get("trend", "?")
    active_exp = experiments.get("active", 0)
    tg_msg = (
        f"🧠 **Morning Brief — {TODAY}**\n"
        f"💰 MRR: {mrr_str} | Velocity: {vel}\n"
        f"🐦 Followers: {followers}\n"
        f"🧪 Active experiments: {active_exp}\n"
    )
    if experiments.get("active", 0) == 0 and experiments.get("queued", 0) == 0:
        tg_msg += "⚠️ No active experiments — initiative generator should queue one today.\n"
    send_telegram(tg_msg)

if __name__ == "__main__":
    main()
