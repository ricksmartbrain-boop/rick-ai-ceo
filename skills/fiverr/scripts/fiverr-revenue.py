#!/usr/bin/env python3
"""Fiverr revenue tracking and reporting."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
FIVERR_DIR = DATA_ROOT / "fiverr"
REVENUE_DIR = FIVERR_DIR / "revenue"
DB_PATH = Path(os.getenv("RICK_DB_PATH", str(DATA_ROOT / "rick.db")))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def revenue_from_workflows(conn: sqlite3.Connection) -> list[dict]:
    """Pull completed Fiverr order workflows with revenue data."""
    rows = conn.execute(
        """
        SELECT id, title, context_json, created_at, finished_at
        FROM workflows
        WHERE kind = 'fiverr_order'
          AND status = 'done'
        ORDER BY finished_at DESC
        """
    ).fetchall()
    orders = []
    for row in rows:
        ctx = json.loads(row["context_json"]) if row["context_json"] else {}
        orders.append({
            "workflow_id": row["id"],
            "title": row["title"],
            "amount_usd": ctx.get("amount_usd", 0),
            "buyer": ctx.get("buyer_username", ""),
            "gig_type": ctx.get("gig_type", ""),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        })
    return orders


def revenue_summary(conn: sqlite3.Connection) -> dict:
    """Generate revenue summary with net after Fiverr's 20% fee."""
    # Try shared function first, fall back to local calculation
    try:
        from runtime.engine import fiverr_revenue_summary
        shared = fiverr_revenue_summary(conn)
    except ImportError:
        shared = None

    orders = revenue_from_workflows(conn)
    gross = sum(o["amount_usd"] for o in orders)
    net = gross * 0.80  # 20% Fiverr fee
    now = datetime.now()

    # This month
    month_start = now.strftime("%Y-%m-01")
    this_month_gross = sum(
        o["amount_usd"] for o in orders
        if o.get("finished_at", "") >= month_start
    )
    this_month_net = this_month_gross * 0.80

    return {
        "total_revenue_usd": gross,
        "total_net_usd": net,
        "this_month_usd": this_month_gross,
        "this_month_net_usd": this_month_net,
        "completed_orders": shared["completed_orders"] if shared else len(orders),
        "active_orders": shared["active_orders"] if shared else conn.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'fiverr_order' AND status IN ('queued', 'active', 'blocked')"
        ).fetchone()["c"],
        "live_gigs": shared["live_gigs"] if shared else conn.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'fiverr_gig_launch' AND status IN ('done', 'launch-ready')"
        ).fetchone()["c"],
        "recent_orders": orders[:5],
    }


def save_snapshot(summary: dict) -> Path:
    """Save revenue snapshot to disk."""
    REVENUE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = REVENUE_DIR / f"snapshot-{stamp}.json"
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return path


def format_summary(summary: dict) -> str:
    """Format revenue summary for Telegram."""
    lines = [
        "Fiverr Revenue",
        f"Gross: ${summary['total_revenue_usd']:.2f}",
        f"Net (after 20% fee): ${summary.get('total_net_usd', summary['total_revenue_usd'] * 0.80):.2f}",
        f"This month gross: ${summary['this_month_usd']:.2f}",
        f"This month net: ${summary.get('this_month_net_usd', summary['this_month_usd'] * 0.80):.2f}",
        f"Completed orders: {summary['completed_orders']}",
        f"Active orders: {summary['active_orders']}",
        f"Live gigs: {summary['live_gigs']}",
    ]
    if summary["recent_orders"]:
        lines.append("\nRecent:")
        for o in summary["recent_orders"]:
            lines.append(f"  ${o['amount_usd']:.0f} — {o['title'][:50]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fiverr revenue tracking")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--save", action="store_true", help="Save snapshot to disk")
    args = parser.parse_args()

    conn = get_connection()
    summary = revenue_summary(conn)
    conn.close()

    if args.save:
        path = save_snapshot(summary)
        print(f"Saved snapshot: {path}")

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(format_summary(summary))


if __name__ == "__main__":
    main()
