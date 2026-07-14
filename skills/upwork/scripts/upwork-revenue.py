#!/usr/bin/env python3
"""Upwork revenue tracking and reporting."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
UPWORK_DIR = DATA_ROOT / "upwork"
REVENUE_DIR = UPWORK_DIR / "revenue"
DB_PATH = Path(os.getenv("RICK_DB_PATH", str(DATA_ROOT / "rick.db")))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def revenue_from_workflows(conn: sqlite3.Connection) -> list[dict]:
    """Pull completed Upwork contract workflows with revenue data."""
    rows = conn.execute(
        """
        SELECT id, title, context_json, created_at, finished_at
        FROM workflows
        WHERE kind = 'upwork_contract'
          AND status = 'done'
        ORDER BY finished_at DESC
        """
    ).fetchall()
    contracts = []
    for row in rows:
        ctx = json.loads(row["context_json"]) if row["context_json"] else {}
        amount = float(ctx.get("fixed_price", 0) or 0)
        if not amount:
            amount = float(ctx.get("hourly_rate", 0) or 0) * float(ctx.get("hours_worked", 0) or 0)
        contracts.append({
            "workflow_id": row["id"],
            "title": row["title"],
            "amount_usd": amount,
            "client": ctx.get("client_username", ""),
            "contract_id": ctx.get("contract_id", ""),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        })
    return contracts


def revenue_summary(conn: sqlite3.Connection) -> dict:
    """Generate revenue summary with net after Upwork's tiered fees."""
    try:
        from runtime.engine import upwork_revenue_summary
        shared = upwork_revenue_summary(conn)
    except ImportError:
        shared = None

    contracts = revenue_from_workflows(conn)
    gross = sum(c["amount_usd"] for c in contracts)

    def _net_for_client(cg: float) -> float:
        return cg * 0.80 if cg <= 500 else 500 * 0.80 + (cg - 500) * 0.90

    # Upwork tiered fees are per-client: 20% on first $500, 10% on $500+
    from collections import defaultdict
    by_client: dict[str, float] = defaultdict(float)
    for c in contracts:
        by_client[c.get("client", "unknown")] += c["amount_usd"]
    net = sum(_net_for_client(cg) for cg in by_client.values())
    now = datetime.now()

    month_start = now.strftime("%Y-%m-01")
    this_month_contracts = [c for c in contracts if c.get("finished_at", "") >= month_start]
    this_month_gross = sum(c["amount_usd"] for c in this_month_contracts)
    by_client_month: dict[str, float] = defaultdict(float)
    for c in this_month_contracts:
        by_client_month[c.get("client", "unknown")] += c["amount_usd"]
    this_month_net = sum(_net_for_client(cg) for cg in by_client_month.values())

    # Proposal stats
    proposals_submitted = conn.execute(
        "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'upwork_proposal' AND status = 'done'"
    ).fetchone()["c"]
    win_rate = (len(contracts) / proposals_submitted * 100) if proposals_submitted > 0 else 0.0

    return {
        "total_revenue_usd": gross,
        "total_net_usd": net,
        "this_month_usd": this_month_gross,
        "this_month_net_usd": this_month_net,
        "completed_contracts": shared["completed_contracts"] if shared else len(contracts),
        "active_contracts": shared["active_contracts"] if shared else conn.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'upwork_contract' AND status IN ('queued', 'active', 'blocked')"
        ).fetchone()["c"],
        "proposals_submitted": proposals_submitted,
        "win_rate_pct": win_rate,
        "recent_contracts": contracts[:5],
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
        "Upwork Revenue",
        f"Gross: ${summary['total_revenue_usd']:.2f}",
        f"Net (after Upwork fees): ${summary.get('total_net_usd', 0):.2f}",
        f"This month gross: ${summary['this_month_usd']:.2f}",
        f"This month net: ${summary.get('this_month_net_usd', 0):.2f}",
        f"Completed contracts: {summary['completed_contracts']}",
        f"Active contracts: {summary['active_contracts']}",
        f"Proposals submitted: {summary['proposals_submitted']}",
        f"Win rate: {summary['win_rate_pct']:.1f}%",
    ]
    if summary["recent_contracts"]:
        lines.append("\nRecent:")
        for c in summary["recent_contracts"]:
            lines.append(f"  ${c['amount_usd']:.0f} -- {c['title'][:50]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upwork revenue tracking")
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
