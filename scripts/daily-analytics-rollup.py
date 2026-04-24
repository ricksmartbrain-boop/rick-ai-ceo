#!/usr/bin/env python3
"""Daily analytics rollup — fires daily 09:00 PT, dispatches the
`daily_analytics_rollup` event so Kade (gemini-3.1-pro analytics persona)
runs her insight-generation pass.

Reads aggregated state from local sources:
  - effective_patterns (self-learning loop)
  - workflows (24h done/cancelled/active)
  - outcomes (24h cost, by route)
  - prospect_pipeline (lead state)
  - notification_dedupe (alert health)
  - revenue/daily-*.json (current MRR snapshot)

Then fires `daily_analytics_rollup` event with the rollup payload. The
event-reactions wiring + delegation_rules.routing routes it to Kade for
analysis — Kade returns INSIGHTS only (never mutates campaigns/budgets).

If Kade is not yet wired or the event has no handler, this script is a
safe no-op — the rollup payload is also written to a JSONL audit trail
so downstream consumers (or manual review) can pick it up.

Override: RICK_KADE_DISABLED=1 to silence dispatch.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.db import connect

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ROLLUP_LOG = DATA_ROOT / "operations" / "daily-analytics-rollup.jsonl"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _gather_rollup(con: sqlite3.Connection) -> dict:
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    rollup: dict = {"generated_at": _now_iso(), "since": cutoff}

    # Workflow funnel
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS c FROM workflows WHERE updated_at >= ? GROUP BY status",
            (cutoff,),
        ).fetchall()
        rollup["workflows_24h"] = {r["status"]: r["c"] for r in rows}
    except Exception as exc:
        rollup["workflows_24h_error"] = str(exc)[:200]

    # Cost by route
    try:
        rows = con.execute(
            "SELECT route, COUNT(*) AS n, ROUND(SUM(cost_usd),4) AS sum_cost "
            "FROM outcomes WHERE created_at >= ? AND cost_usd > 0 "
            "GROUP BY route ORDER BY sum_cost DESC LIMIT 10",
            (cutoff,),
        ).fetchall()
        rollup["cost_by_route_24h"] = [dict(r) for r in rows]
    except Exception as exc:
        rollup["cost_by_route_24h_error"] = str(exc)[:200]

    # Self-learning loop health
    try:
        row = con.execute(
            "SELECT COUNT(*) AS total, "
            "       SUM(CASE WHEN sum_runs > 0 THEN 1 ELSE 0 END) AS used, "
            "       SUM(sum_wins) AS wins, "
            "       SUM(sum_runs) AS runs "
            "FROM effective_patterns"
        ).fetchone()
        rollup["self_learning"] = {
            "patterns_total": int(row["total"] or 0) if row else 0,
            "patterns_used": int(row["used"] or 0) if row else 0,
            "credit_runs": int(row["runs"] or 0) if row else 0,
            "credit_wins": int(row["wins"] or 0) if row else 0,
        }
    except Exception as exc:
        rollup["self_learning_error"] = str(exc)[:200]

    # Notification dedup health (proxy for alert noise reduction)
    try:
        row = con.execute(
            "SELECT COUNT(*) AS unique_kinds, "
            "       COALESCE(SUM(total_seen), 0) AS total_seen, "
            "       COALESCE(SUM(count_since_alert), 0) AS suppressed "
            "FROM notification_dedupe"
        ).fetchone()
        rollup["notification_dedup"] = {
            "unique_kinds": int(row["unique_kinds"] or 0) if row else 0,
            "total_alerts_seen": int(row["total_seen"] or 0) if row else 0,
            "currently_suppressed": int(row["suppressed"] or 0) if row else 0,
        }
    except Exception as exc:
        rollup["notification_dedup_error"] = str(exc)[:200]

    # Hot prospects
    try:
        rows = con.execute(
            "SELECT username, platform, score FROM prospect_pipeline "
            "WHERE score >= 7 ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        rollup["hot_prospects"] = [dict(r) for r in rows]
    except Exception:
        rollup["hot_prospects"] = []

    # Current MRR snapshot
    today = datetime.now().strftime("%Y-%m-%d")
    snap_path = DATA_ROOT / "revenue" / f"daily-{today}.json"
    if snap_path.exists():
        try:
            rollup["mrr_snapshot"] = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    return rollup


def _log_to_jsonl(entry: dict) -> None:
    ROLLUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ROLLUP_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    con = connect()
    try:
        rollup = _gather_rollup(con)
        _log_to_jsonl({"event": "rollup_generated", **rollup})

        if os.getenv("RICK_KADE_DISABLED", "").strip().lower() in ("1", "true", "yes"):
            print(f"[daily-analytics-rollup] dispatch suppressed (RICK_KADE_DISABLED=1); rollup logged")
            return 0

        # Dispatch to Kade via event-reactions/delegation_rules
        try:
            from runtime.engine import dispatch_event
            dispatch_event(con, None, None, "daily_analytics_rollup", rollup)
            _log_to_jsonl({"event": "rollup_dispatched", "ts": _now_iso(),
                           "patterns_total": rollup.get("self_learning", {}).get("patterns_total", 0)})
            print(f"[daily-analytics-rollup] dispatched to Kade — patterns={rollup.get('self_learning', {}).get('patterns_total', 0)} workflows={sum(rollup.get('workflows_24h', {}).values())}")
        except Exception as exc:
            _log_to_jsonl({"event": "rollup_dispatch_failed", "ts": _now_iso(), "error": str(exc)[:200]})
            print(f"[daily-analytics-rollup] dispatch failed: {exc}", file=sys.stderr)
            return 1
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
