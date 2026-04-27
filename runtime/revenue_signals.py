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


def load_latest_revenue() -> dict:
    """Parse the most recent revenue snapshot from the vault."""
    candidates = sorted(REVENUE_DIR.glob("*.md"))
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
    behind = revenue.get("behind_pct", 0)
    if behind > 30:
        return f"Revenue: BEHIND TARGET by {behind:.0f}% (net ${revenue['net_usd']:.0f} vs target ${revenue['target_usd']:.0f})"
    if behind > 10:
        return f"Revenue: slightly behind ({behind:.0f}% gap, net ${revenue['net_usd']:.0f})"
    return f"Revenue: on track (net ${revenue['net_usd']:.0f}, {behind:.0f}% gap)"
