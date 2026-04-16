#!/usr/bin/env python3
"""Context pack builder for Rick workflows."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time as _time
from datetime import datetime
from pathlib import Path

from runtime.learnings import learnings_summary


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SCORECARD_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_PORTFOLIO_SCORECARDS_FILE", str(DATA_ROOT / "scorecards" / "portfolio.json"))
    )
)
REVENUE_DIR = DATA_ROOT / "revenue"
CONTROL_DIR = DATA_ROOT / "control"
# RICK_MEMORY_DIR lets the memory dir be overridden independently of RICK_DATA_ROOT.
# This is needed when rick-vault is TCC-blocked but ~/clawd/memory/ is directly accessible.
MEMORY_DIR = Path(
    os.path.expanduser(os.getenv("RICK_MEMORY_DIR", str(DATA_ROOT / "memory")))
)
MEMORY_INDEX_FILE = Path(
    os.path.expanduser(os.getenv("RICK_MEMORY_INDEX_FILE", str(DATA_ROOT / "control" / "memory-index.json")))
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_memory_index() -> dict:
    if not MEMORY_INDEX_FILE.exists():
        return {}
    try:
        return json.loads(MEMORY_INDEX_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


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


def top_ranked_projects(limit: int = 10) -> list[dict]:
    ranked = []
    for project in load_scorecards():
        enriched = dict(project)
        enriched["score"] = score_project(project)
        ranked.append(enriched)
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:limit]


def latest_revenue_snapshot() -> dict:
    candidates = sorted(REVENUE_DIR.glob("*.md"))
    if not candidates:
        return {"available": False}

    latest = candidates[-1]
    text = read_text(latest)
    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*([^\|]+)\|", text)
    gap_match = re.search(r"\|\s*Gap\s*\|\s*([^\|]+)\|", text)
    period_match = re.search(r"\*\*Period:\*\*\s*(.+)", text)

    return {
        "available": True,
        "path": str(latest),
        "date": latest.stem,
        "period": period_match.group(1).strip() if period_match else "unknown",
        "net": net_match.group(1).strip() if net_match else "unknown",
        "gap": gap_match.group(1).strip() if gap_match else "n/a",
    }


def open_approval_count() -> int:
    approvals_path = CONTROL_DIR / "approvals.md"
    return sum(1 for line in read_text(approvals_path).splitlines() if line.startswith("|") and "| open |" in line)


def dependency_gap_summary() -> str:
    text = read_text(CONTROL_DIR / "dependency-gaps.md")
    if not text:
        return "unknown"
    if "No missing dependencies detected." in text:
        return "clear"
    if "No gaps recorded yet." in text:
        return "unknown"
    return "attention-needed"


def ops_health_summary() -> str:
    text = read_text(CONTROL_DIR / "ops-health.md")
    if not text:
        return "unknown"
    if "| fail |" in text:
        return "failing"
    if "| warn |" in text:
        return "warnings"
    return "healthy"


def recent_memory(limit: int = 5) -> list[dict]:
    items = sorted(MEMORY_DIR.glob("*.md"))
    results: list[dict] = []
    for path in items[-limit:]:
        text = read_text(path)
        first_heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
        results.append({"path": str(path), "title": first_heading})
    return results


def memory_index_summary() -> dict:
    index = load_memory_index()
    counts = index.get("counts", {}) if isinstance(index, dict) else {}
    tiers = counts.get("tiers", {}) if isinstance(counts, dict) else {}
    generated_at = index.get("generated_at", "") if isinstance(index, dict) else ""
    stale = False
    if generated_at:
        try:
            from datetime import datetime
            gen_time = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
            stale = (datetime.now().astimezone() - gen_time).total_seconds() > 86400
        except (ValueError, TypeError):
            stale = True
    return {
        "generated_at": generated_at,
        "entries": int(counts.get("entries", 0) or 0),
        "tiers": tiers if isinstance(tiers, dict) else {},
        "stale": stale,
    }


def _bm25_related_memory(query: str, limit: int = 3) -> list[dict]:
    """Try BM25 search via memory-search.py for better relevance."""
    import subprocess
    search_script = Path(__file__).resolve().parents[1] / "skills" / "obsidian-memory" / "scripts" / "memory-search.py"
    if not search_script.exists():
        return []
    try:
        result = subprocess.run(
            ["python3", str(search_script), "search", "--query", query, "--limit", str(limit), "--json"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            results = data if isinstance(data, list) else data.get("results", [])
            return [{"path": r.get("path", ""), "title": r.get("title", ""), "tier": r.get("tier", "cold"), "preview": r.get("preview", "")} for r in results[:limit]]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return []


def related_memory_notes(workflow_row: sqlite3.Row, limit: int = 6) -> list[dict]:
    index = load_memory_index()
    entries = index.get("entries", []) if isinstance(index, dict) else []
    if not isinstance(entries, list):
        return []

    try:
        context = json.loads(workflow_row["context_json"])
    except (json.JSONDecodeError, TypeError):
        context = {}
    tokens = {
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-]+", " ".join(
            [
                workflow_row["title"],
                workflow_row["slug"],
                workflow_row["project"],
                context.get("idea", ""),
                context.get("project", ""),
                context.get("product_slug", ""),
            ]
        ))
        if len(token) >= 4
    }
    if not tokens:
        return []

    tier_bonus = {"hot": 6, "warm": 3, "cold": 1}
    scored: list[tuple[float, dict]] = []
    now = datetime.now()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        haystack = " ".join(
            [
                str(entry.get("path", "")),
                str(entry.get("title", "")),
                str(entry.get("project", "")),
                str(entry.get("preview", "")),
                " ".join(entry.get("tags", [])),
                " ".join(entry.get("wikilinks", [])),
            ]
        ).lower()
        matches = sum(1 for token in tokens if token in haystack)
        if matches == 0:
            continue
        score = matches * 4 + tier_bonus.get(str(entry.get("tier", "cold")), 0)
        if entry.get("project") and entry.get("project") == workflow_row["project"]:
            score += 3
        # Memory decay: hot (<7d) 1.0x, warm (8-30d) 0.6x, cold (30d+) 0.2x
        modified = entry.get("modified_at", "")
        if modified:
            try:
                days_old = (now - datetime.fromisoformat(modified.replace("Z", "+00:00").split("+")[0])).days
                if days_old <= 7:
                    decay = 1.0
                elif days_old <= 30:
                    decay = 0.6
                else:
                    decay = 0.2
                score *= decay
            except (ValueError, TypeError):
                pass
        scored.append((score, entry))

    scored.sort(key=lambda item: (item[0], item[1].get("modified_at", "")), reverse=True)
    return [entry for _, entry in scored[:limit]]


def recent_outcomes(connection: sqlite3.Connection, limit: int = 30) -> dict:
    """Query outcomes table for recent execution stats."""
    try:
        rows = connection.execute(
            """
            SELECT step_name, route, outcome_type, cost_usd, duration_seconds,
                   model_used, created_at
            FROM outcomes
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.Error as exc:
        import logging
        logging.getLogger("rick.context").warning("outcomes query failed: %s", exc)
        return {"available": False}

    if not rows:
        return {"available": False}

    total = len(rows)
    successes = sum(1 for r in rows if r["outcome_type"] == "success")
    failures = sum(1 for r in rows if r["outcome_type"] == "failure")
    total_cost = sum(float(r["cost_usd"] or 0) for r in rows)
    avg_cost = total_cost / total if total else 0

    # Per-step stats
    step_stats: dict[str, dict] = {}
    for r in rows:
        step = r["step_name"]
        if step not in step_stats:
            step_stats[step] = {"success": 0, "failure": 0, "total_cost": 0.0, "count": 0}
        step_stats[step]["count"] += 1
        step_stats[step]["total_cost"] += float(r["cost_usd"] or 0)
        if r["outcome_type"] == "success":
            step_stats[step]["success"] += 1
        else:
            step_stats[step]["failure"] += 1

    return {
        "available": True,
        "total": total,
        "success_rate": round(successes / total * 100, 1) if total else 0,
        "failures": failures,
        "avg_cost_usd": round(avg_cost, 4),
        "total_cost_usd": round(total_cost, 4),
        "step_stats": {
            k: {
                "success_rate": round(v["success"] / v["count"] * 100, 1) if v["count"] else 0,
                "avg_cost": round(v["total_cost"] / v["count"], 4) if v["count"] else 0,
                "count": v["count"],
            }
            for k, v in step_stats.items()
        },
    }


def workflow_artifacts(connection: sqlite3.Connection, workflow_id: str, limit: int = 10) -> list[dict]:
    rows = connection.execute(
        """
        SELECT kind, title, path, created_at
        FROM artifacts
        WHERE workflow_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (workflow_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def runtime_lane_snapshot(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
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
    return [dict(row) for row in rows]


_context_pack_cache: dict[str, tuple[float, dict]] = {}
_CONTEXT_PACK_TTL = 28800.0


def _compact_revenue() -> dict:
    """Return a compact revenue summary instead of the full snapshot."""
    try:
        from runtime.revenue_signals import revenue_context_line
        line = revenue_context_line()
        if line:
            return {"available": True, "summary": line}
    except Exception as exc:
        import logging
        logging.getLogger("rick.context").warning("revenue_context_line failed: %s", exc)
    snapshot = latest_revenue_snapshot()
    if snapshot.get("available"):
        return {"available": True, "summary": f"{snapshot['date']}: net={snapshot['net']} gap={snapshot['gap']}"}
    return {"available": False}


def build_context_pack(connection: sqlite3.Connection, workflow_row: sqlite3.Row, step_name: str | None = None) -> dict:
    wf_id = workflow_row["id"]
    now = _time.monotonic()
    cached = _context_pack_cache.get(wf_id)
    if cached is not None and (now - cached[0]) < _CONTEXT_PACK_TTL:
        return cached[1]

    try:
        context = json.loads(workflow_row["context_json"])
    except (json.JSONDecodeError, TypeError):
        context = {}

    # Skip memory lookup for publish steps (not needed, saves tokens)
    if step_name and step_name.startswith("publish_"):
        related = []
    else:
        related = _bm25_related_memory(
            f"{workflow_row['title']} {workflow_row['project']}",
            limit=20,
        ) or related_memory_notes(workflow_row, limit=25)

    pack = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "workflow": {
            "id": workflow_row["id"],
            "kind": workflow_row["kind"],
            "title": workflow_row["title"],
            "slug": workflow_row["slug"],
            "lane": workflow_row["lane"],
            "status": workflow_row["status"],
            "stage": workflow_row["stage"],
            "project": workflow_row["project"],
        },
        "workflow_context": context,
        "business": {
            "top_ranked_projects": top_ranked_projects(),
            "latest_revenue": _compact_revenue(),
            "open_approvals": open_approval_count(),
            "dependency_gaps": dependency_gap_summary(),
            "ops_health": ops_health_summary(),
            "runtime_lanes": runtime_lane_snapshot(connection),
        },
        "memory_index": memory_index_summary(),
        "recent_memory": recent_memory(limit=20),
        "related_memory": related,
        "artifacts": workflow_artifacts(connection, workflow_row["id"], limit=20),
        "outcomes": recent_outcomes(connection, limit=30),
        "learnings": learnings_summary(),
    }
    _context_pack_cache[wf_id] = (now, pack)
    return pack


def render_context_markdown(context_pack: dict) -> str:
    workflow = context_pack["workflow"]
    workflow_context = context_pack["workflow_context"]
    business = context_pack["business"]
    lines = [
        f"# Context Pack — {workflow['title']}",
        "",
        f"- Workflow ID: {workflow['id']}",
        f"- Kind: {workflow['kind']}",
        f"- Status: {workflow['status']}",
        f"- Stage: {workflow['stage']}",
        f"- Lane: {workflow['lane']}",
        f"- Project: {workflow['project']}",
        "",
        "## Workflow Context",
    ]
    for key, value in sorted(workflow_context.items()):
        lines.append(f"- {key}: {value}")

    lines.extend(
        [
            "",
            "## Business State",
            f"- Open approvals: {business['open_approvals']}",
            f"- Dependency gaps: {business['dependency_gaps']}",
            f"- Ops health: {business['ops_health']}",
        ]
    )

    if business["runtime_lanes"]:
        lane_bits = [
            f"{item['lane']} q{item['queued_jobs']}/r{item['running_jobs']}/b{item['blocked_jobs']}"
            for item in business["runtime_lanes"]
        ]
        lines.append(f"- Runtime lanes: {', '.join(lane_bits)}")
    memory_index = context_pack["memory_index"]
    if memory_index["entries"]:
        stale_warning = " [STALE — >24h since last index rebuild]" if memory_index.get("stale") else ""
        lines.append(
            f"- Memory index: {memory_index['entries']} entries ({memory_index['tiers'].get('hot', 0)} hot / {memory_index['tiers'].get('warm', 0)} warm / {memory_index['tiers'].get('cold', 0)} cold){stale_warning}"
        )

    revenue = business["latest_revenue"]
    if revenue["available"]:
        if "summary" in revenue:
            lines.append(f"- Latest revenue snapshot: {revenue['summary']}")
        else:
            lines.extend(
                [
                    f"- Latest revenue snapshot: {revenue.get('date', 'unknown')} ({revenue.get('period', 'unknown')})",
                    f"- Period net revenue: {revenue.get('net', 'unknown')}",
                    f"- Gap: {revenue.get('gap', 'unknown')}",
                ]
            )
    else:
        lines.append("- Latest revenue snapshot: none")

    lines.extend(["", "## Top Ranked Projects"])
    for project in business["top_ranked_projects"]:
        lines.append(f"- {project['name']} ({project['score']:.2f}) — {project.get('notes', '')}")

    lines.extend(["", "## Recent Memory"])
    for item in context_pack["recent_memory"]:
        lines.append(f"- {item['title']} — {item['path']}")

    if context_pack["related_memory"]:
        lines.extend(["", "## Related Memory"])
        for item in context_pack["related_memory"]:
            lines.append(f"- [{item['tier']}] {item['title']} — {item['path']}")

    if context_pack["artifacts"]:
        lines.extend(["", "## Existing Workflow Artifacts"])
        for artifact in context_pack["artifacts"]:
            lines.append(f"- {artifact['kind']}: {artifact['title']} — {artifact['path']}")

    outcomes = context_pack.get("outcomes", {})
    if outcomes.get("available"):
        lines.extend(["", "## Recent Outcomes"])
        lines.append(f"- Success rate: {outcomes['success_rate']}% ({outcomes['total']} recent jobs)")
        lines.append(f"- Avg cost: ${outcomes['avg_cost_usd']:.4f}")
        if outcomes.get("step_stats"):
            for step, stats in list(outcomes["step_stats"].items())[:5]:
                lines.append(f"  - {step}: {stats['success_rate']}% success, ${stats['avg_cost']:.4f} avg, {stats['count']} runs")

    learnings_data = context_pack.get("learnings", {})
    if learnings_data.get("available"):
        lines.extend(["", "## Recent Learnings"])
        if learnings_data.get("top_failures"):
            lines.append(f"- Failure patterns: {', '.join(str(f) for f in learnings_data['top_failures'][:3])}")
        if learnings_data.get("top_wins"):
            lines.append(f"- Wins: {', '.join(str(w) for w in learnings_data['top_wins'][:3])}")
        lines.append(f"- Corrective actions pending: {learnings_data.get('corrective_count', 0)}")

    return "\n".join(lines) + "\n"
