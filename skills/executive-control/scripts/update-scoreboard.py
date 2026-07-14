#!/usr/bin/env python3
"""Refresh Rick's high-level operating scoreboard."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
CONTROL_DIR = DATA_ROOT / "control"
BRIEFINGS_DIR = CONTROL_DIR / "briefings"
REVENUE_DIR = DATA_ROOT / "revenue"
DASHBOARD_FILE = DATA_ROOT / "dashboards" / "scoreboard.md"
SCORECARD_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_PORTFOLIO_SCORECARDS_FILE", str(DATA_ROOT / "scorecards" / "portfolio.json"))
    )
)
EXECUTION_LEDGER_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_EXECUTION_LEDGER_FILE", str(DATA_ROOT / "operations" / "execution-ledger.jsonl"))
    )
)
TOKEN_USAGE_FILE = Path(
    os.path.expanduser(os.getenv("RICK_LLM_USAGE_LOG_FILE", str(DATA_ROOT / "operations" / "llm-usage.jsonl")))
)
RUNTIME_DB_FILE = Path(
    os.path.expanduser(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def load_jsonl(path: Path, days: int | None = None) -> list[dict]:
    if not path.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days) if days is not None else None
    rows: list[dict] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        stamp = parse_timestamp(payload.get("timestamp"))
        if cutoff is not None and (stamp is None or stamp < cutoff):
            continue
        rows.append(payload)
    return rows


def latest_markdown(root: Path) -> Path | None:
    files = sorted(root.glob("*.md"))
    return files[-1] if files else None


def latest_revenue_snapshot() -> dict:
    candidates = sorted(p for p in REVENUE_DIR.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem))
    if not candidates:
        return {"available": False}

    latest = candidates[-1]
    text = read_text(latest)
    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*\$?([\d,.\-]+)", text)
    gap_match = re.search(r"\|\s*Gap\s*\|\s*([^\|]+)\|", text)
    period_match = re.search(r"\*\*Period:\*\*\s*(.+)", text)

    def parse_net(path: Path) -> float:
        match = re.search(r"\|\s*Period Net Revenue\s*\|\s*\$?([\d,.\-]+)", read_text(path))
        if not match:
            return 0.0
        try:
            return float(match.group(1).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0

    velocity_path = REVENUE_DIR / "velocity.json"
    try:
        velocity = json.loads(read_text(velocity_path)) if velocity_path.exists() else {}
    except json.JSONDecodeError:
        velocity = {}

    return {
        "available": True,
        "path": latest,
        "date": latest.stem,
        "period": period_match.group(1).strip() if period_match else "unknown",
        "net": net_match.group(1).strip() if net_match else "unknown",
        "rev_7d": f"${sum(parse_net(path) for path in candidates[-7:]):.2f}",
        "mrr": f"${float(velocity.get('current_mrr', 0.0) or 0.0):.2f}",
        "mrr_delta_7d": f"${float(velocity.get('delta_7d', 0.0) or 0.0):+.2f}",
        "gap": gap_match.group(1).strip() if gap_match else "n/a",
    }


def count_open_approvals() -> int:
    # Count from the runtime DB (source of truth), not the approvals.md mirror —
    # the mirror goes stale and under-reported open approvals (2026-07-14).
    # Same query as runtime_stats() below.
    if not RUNTIME_DB_FILE.exists():
        return 0
    connection = sqlite3.connect(str(RUNTIME_DB_FILE))
    try:
        return connection.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'open'"
        ).fetchone()[0]
    finally:
        connection.close()


def dependency_gap_status() -> str:
    text = read_text(CONTROL_DIR / "dependency-gaps.md")
    if not text or "No gaps recorded yet." in text:
        return "unknown"
    if "No missing dependencies detected." in text:
        return "clear"
    return "attention-needed"


def ops_health_status() -> str:
    text = read_text(CONTROL_DIR / "ops-health.md")
    if not text or "No checks run yet." in text:
        return "unknown"
    if "| fail |" in text:
        return "failing checks"
    if "| warn |" in text:
        return "warnings present"
    return "healthy"


def load_scorecards() -> list[dict]:
    if not SCORECARD_FILE.exists():
        return []
    try:
        payload = json.loads(SCORECARD_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    projects = payload.get("projects", [])
    return projects if isinstance(projects, list) else []


def score_project(project: dict) -> float:
    status_multiplier = {
        "active": 1.0,
        "explore": 0.9,
        "nurture": 0.85,
        "paused": 0.6,
    }
    weighted = (
        project.get("revenue_now", 0) * 0.26
        + project.get("growth_potential", 0) * 0.18
        + project.get("speed_to_cash", 0) * 0.18
        + project.get("distribution_fit", 0) * 0.12
        + project.get("confidence", 0) * 0.12
        + project.get("strategic_fit", 0) * 0.14
        - project.get("maintenance_load", 0) * 0.10
    )
    return round(weighted * status_multiplier.get(project.get("status", "active"), 1.0), 2)


def ranked_projects() -> list[dict]:
    ranked = []
    for project in load_scorecards():
        enriched = dict(project)
        enriched["score"] = score_project(project)
        ranked.append(enriched)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def execution_stats(days: int = 7) -> dict:
    events = load_jsonl(EXECUTION_LEDGER_FILE, days=days)
    if not events:
        return {"count": 0, "latest": None, "kinds": {}}
    kinds = Counter(event.get("kind", "unknown") for event in events)
    return {"count": len(events), "latest": events[-1], "kinds": dict(kinds)}


def token_stats(days: int = 7) -> dict:
    events = load_jsonl(TOKEN_USAGE_FILE, days=days)
    today = datetime.now().date()
    today_spend = 0.0
    by_bucket: dict[str, float] = defaultdict(float)
    for event in events:
        stamp = parse_timestamp(event.get("timestamp"))
        usd = float(event.get("usd", 0) or 0)
        if stamp and stamp.date() == today:
            today_spend += usd
        by_bucket[str(event.get("bucket", "unknown"))] += usd
    top_bucket = None
    if by_bucket:
        top_bucket = max(by_bucket.items(), key=lambda item: item[1])
    return {
        "count": len(events),
        "today_spend": round(today_spend, 2),
        "period_spend": round(sum(float(event.get("usd", 0) or 0) for event in events), 2),
        "top_bucket": top_bucket,
    }


def runtime_stats() -> dict:
    if not RUNTIME_DB_FILE.exists():
        return {"available": False}
    connection = sqlite3.connect(str(RUNTIME_DB_FILE))
    connection.row_factory = sqlite3.Row
    try:
        workflow_counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM workflows GROUP BY status"
            ).fetchall()
        }
        job_counts = {
            row["status"]: row["count"]
            for row in connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        }
        open_approvals = connection.execute(
            "SELECT COUNT(*) AS count FROM approvals WHERE status = 'open'"
        ).fetchone()["count"]
        lane_rows = connection.execute(
            """
            SELECT lane,
                   SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) AS queued_jobs,
                   SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_jobs,
                   SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_jobs
            FROM jobs
            GROUP BY lane
            ORDER BY lane ASC
            """
        ).fetchall()
        return {
            "available": True,
            "workflow_counts": workflow_counts,
            "job_counts": job_counts,
            "open_approvals": open_approvals,
            "lanes": [dict(row) for row in lane_rows],
        }
    finally:
        connection.close()


def build_scoreboard() -> str:
    revenue = latest_revenue_snapshot()
    exec_stats = execution_stats()
    spend_stats = token_stats()
    runtime = runtime_stats()
    latest_brief = latest_markdown(BRIEFINGS_DIR)
    ranked = ranked_projects()

    lines = [
        "# Scoreboard",
        "",
        f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"- Ops health: {ops_health_status()}",
        f"- Open approvals: {count_open_approvals()}",
        f"- Dependency gaps: {dependency_gap_status()}",
    ]

    if latest_brief is not None:
        lines.append(f"- Latest briefing: {latest_brief.name}")
    else:
        lines.append("- Latest briefing: none yet")

    lines.extend(["", "## Revenue"])
    if revenue["available"]:
        lines.extend(
            [
                f"- Latest snapshot: {revenue['date']} ({revenue['period']})",
                f"- MRR: {revenue.get('mrr', 'unknown')}",
                f"- Revenue last 7d: {revenue.get('rev_7d', 'unknown')}",
                f"- MRR delta 7d: {revenue.get('mrr_delta_7d', 'unknown')}",
                f"- Period net revenue: {revenue['net']}",
                f"- Gap: {revenue['gap']}",
            ]
        )
    else:
        lines.append("- No revenue snapshot found yet.")

    lines.extend(
        [
            "",
            "## Token Economics",
            f"- Today spend: ${spend_stats['today_spend']:.2f}",
            f"- Last 7d spend: ${spend_stats['period_spend']:.2f}",
            f"- Logged usage events: {spend_stats['count']}",
        ]
    )
    if spend_stats["top_bucket"] is not None:
        bucket, spend = spend_stats["top_bucket"]
        lines.append(f"- Highest-spend bucket: {bucket} (${spend:.2f})")

    lines.extend(
        [
            "",
            "## Execution",
            f"- Logged execution events (7d): {exec_stats['count']}",
        ]
    )
    if exec_stats["latest"] is not None:
        latest = exec_stats["latest"]
        lines.append(
            f"- Latest event: {latest.get('kind', 'unknown')} / {latest.get('status', 'unknown')} / {latest.get('title', '')}"
        )
    if exec_stats["kinds"]:
        kind_bits = [f"{kind}: {count}" for kind, count in sorted(exec_stats["kinds"].items())]
        lines.append(f"- Mix: {', '.join(kind_bits)}")

    lines.extend(["", "## Runtime"])
    if runtime["available"]:
        workflow_bits = ", ".join(f"{status}: {count}" for status, count in sorted(runtime["workflow_counts"].items())) or "none"
        job_bits = ", ".join(f"{status}: {count}" for status, count in sorted(runtime["job_counts"].items())) or "none"
        lines.append(f"- Workflows: {workflow_bits}")
        lines.append(f"- Jobs: {job_bits}")
        lines.append(f"- Open runtime approvals: {runtime['open_approvals']}")
        if runtime["lanes"]:
            lane_bits = [
                f"{lane['lane']} q{lane['queued_jobs']}/r{lane['running_jobs']}/b{lane['blocked_jobs']}"
                for lane in runtime["lanes"]
            ]
            lines.append(f"- Lane mix: {', '.join(lane_bits)}")
    else:
        lines.append("- Runtime DB not initialized yet.")

    lines.extend(
        [
            "",
            "## Portfolio Ranking",
            "",
            "| Rank | Project | Score | Status |",
            "|------|---------|-------|--------|",
        ]
    )
    for index, project in enumerate(ranked[:5], start=1):
        lines.append(
            f"| {index} | {project.get('name', project.get('slug', 'unknown'))} | {project['score']:.2f} | {project.get('status', 'active')} |"
        )

    if not ranked:
        lines.append("| - | No scorecards loaded | - | - |")

    return "\n".join(lines) + "\n"


def main() -> int:
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    body = build_scoreboard()
    DASHBOARD_FILE.write_text(body, encoding="utf-8")
    print(f"Updated {DASHBOARD_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
