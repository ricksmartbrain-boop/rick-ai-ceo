#!/usr/bin/env python3
"""Revenue signal processing for Rick v6 autonomous priority adjustment."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
REVENUE_DIR = DATA_ROOT / "revenue"

# Stripe product IDs that count as Rick-attributable revenue (2026-07-13
# attribution-truth v2). The shared Stripe account holds ~50 of Vlad's
# businesses — legacy Folderly 'Credits' subs (the $538 phantom), personal
# mentorship invoices, comped internal invoices — any MRR/cash computation
# that sums the raw account is wrong by construction.
#
# RICK MRR = meetrick products ONLY. Allowlist mirrors the stripe-webhook
# default in ~/meetrick/api/src/routes/stripe-webhook.js — keep in sync.
RICK_PRODUCT_IDS = frozenset({
    "prod_UAnyfcxSShF33a",  # Rick Pro (live $29/mo payment link)
    "prod_UAbJqha8GV2lDC",  # Rick Pro (legacy links)
    "prod_URTpF3D8hAHUmX",  # Rick Pro (2026 series)
    "prod_URTpx3MtWC0WYB",  # Rick Starter
    "prod_URTptzJL9gfGAT",  # Rick Custom
    "prod_U8mgNHBNVO9Zsy",  # Managed AI CEO — Monthly ($499)
    "prod_U8mgdTxX1zzryq",  # AI CEO Setup — Done For You ($2,500 deploy tier)
})

# PORTFOLIO (not Rick's revenue): products Rick handles fulfillment ops for
# but did NOT acquire and must NEVER count in Rick MRR. LinguaLive is
# Khrystyna's product — track as "portfolio (not Rick's)" everywhere.
PORTFOLIO_PRODUCT_IDS = frozenset({
    "prod_TV7oz6jtR1ejfd",  # LinguaLive Subscription (owner: Khrystyna)
})

# Compatibility alias — OPS-WATCH SCOPE ONLY (fulfillment/status polling,
# e.g. scripts/stripe-poll.py watches both sets because Rick runs LinguaLive
# fulfillment operationally). NEVER use this union for MRR attribution:
# report rick_mrr (RICK_PRODUCT_IDS) and portfolio_mrr (PORTFOLIO_PRODUCT_IDS)
# as SEPARATE numbers, never summed.
RICK_REAL_PRODUCT_IDS = RICK_PRODUCT_IDS | PORTFOLIO_PRODUCT_IDS


def _dated_reports() -> list[Path]:
    return sorted(p for p in REVENUE_DIR.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))


def _parse_period_net(path: Path) -> float:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*([^\|]+)\|", text)
    if not net_match:
        return 0.0
    cleaned = re.sub(r"[^\d.\-]", "", net_match.group(1))
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def _velocity() -> dict:
    path = REVENUE_DIR / "velocity.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_latest_revenue() -> dict:
    """Parse the most recent revenue snapshot from the vault."""
    candidates = _dated_reports()
    if not candidates:
        return {"available": False}

    latest = candidates[-1]
    try:
        text = latest.read_text(encoding="utf-8")
    except OSError:
        return {"available": False}

    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*([^\|]+)\|", text)
    gap_match = re.search(r"\|\s*Gap\s*\|\s*([^\|]+)\|", text)
    target_match = re.search(r"\|\s*Target\s*\|\s*([^\|]+)\|", text)

    net_str = net_match.group(1).strip() if net_match else ""
    gap_str = gap_match.group(1).strip() if gap_match else ""
    target_str = target_match.group(1).strip() if target_match else ""

    def parse_usd(s: str) -> float:
        cleaned = re.sub(r"[^\d.\-]", "", s)
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    net_usd = parse_usd(net_str)
    target_usd = parse_usd(target_str)
    gap_usd = parse_usd(gap_str)

    behind_pct = 0.0
    if target_usd > 0:
        behind_pct = max(0.0, (target_usd - net_usd) / target_usd * 100)

    return {
        "available": True,
        "path": str(latest),
        "date": latest.stem,
        "net_usd": net_usd,
        "rev_7d_usd": round(sum(_parse_period_net(path) for path in candidates[-7:]), 2),
        "mrr_usd": float(_velocity().get("current_mrr", 0.0) or 0.0),  # Rick-only (meetrick)
        "portfolio_mrr_usd": float(_velocity().get("portfolio_mrr", 0.0) or 0.0),  # LinguaLive etc — NOT Rick's
        "mrr_delta_7d": float(_velocity().get("delta_7d", 0.0) or 0.0),
        "target_usd": target_usd,
        "gap_usd": gap_usd,
        "behind_pct": round(behind_pct, 1),
    }


def adjust_priorities(connection: sqlite3.Connection) -> dict:
    """If behind revenue target by >30%, boost product/customer lanes, deprioritize research.

    Returns a dict describing what was changed (if anything).
    """
    revenue = load_latest_revenue()
    if not revenue.get("available"):
        return {"adjusted": False, "reason": "no revenue data"}

    behind_pct = revenue.get("behind_pct", 0.0)
    if behind_pct <= 30.0:
        return {"adjusted": False, "reason": f"on track ({behind_pct:.1f}% behind, threshold 30%)"}

    # Boost product and customer lanes by moving queued workflows to higher priority
    now = datetime.now().isoformat(timespec="seconds")
    boosted = 0

    for lane in ("product-lane", "customer-lane"):
        cursor = connection.execute(
            """
            UPDATE workflows
            SET priority = MAX(1, priority - 10), updated_at = ?
            WHERE lane = ? AND status IN ('queued', 'active', 'blocked')
            AND priority > 10
            """,
            (now, lane),
        )
        boosted += cursor.rowcount

    # Deprioritize research lane
    deprioritized = 0
    cursor = connection.execute(
        """
        UPDATE workflows
        SET priority = MIN(99, priority + 10), updated_at = ?
        WHERE lane = 'research-lane' AND status IN ('queued', 'active')
        AND priority < 90
        """,
        (now,),
    )
    deprioritized = cursor.rowcount

    connection.commit()
    return {
        "adjusted": True,
        "behind_pct": behind_pct,
        "boosted_workflows": boosted,
        "deprioritized_workflows": deprioritized,
    }


def revenue_context_line() -> str:
    """One-line revenue status for context packs and briefs."""
    revenue = load_latest_revenue()
    if not revenue.get("available"):
        return "Revenue data: unavailable"
    if revenue.get("mrr_usd") is not None:
        return (
            f"Revenue: Rick MRR ${revenue['mrr_usd']:.2f} (meetrick only) | "
            f"portfolio ops ${revenue.get('portfolio_mrr_usd', 0.0):.2f} (LinguaLive, not Rick's) | "
            f"rev_7d ${revenue.get('rev_7d_usd', 0.0):.2f} | "
            f"MRR Δ7d ${revenue.get('mrr_delta_7d', 0.0):+.2f}"
        )
    behind = revenue.get("behind_pct", 0)
    if behind > 30:
        return f"Revenue: BEHIND TARGET by {behind:.0f}% (net ${revenue['net_usd']:.0f} vs target ${revenue['target_usd']:.0f})"
    if behind > 10:
        return f"Revenue: slightly behind ({behind:.0f}% gap, net ${revenue['net_usd']:.0f})"
    return f"Revenue: on track (net ${revenue['net_usd']:.0f}, {behind:.0f}% gap)"
