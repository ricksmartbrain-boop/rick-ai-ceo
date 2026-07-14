#!/usr/bin/env python3
"""Generate a concise morning brief from Rick's latest operating artifacts."""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern))
    return files[-1] if files else None


def extract_plan_items(note_path: Path) -> list[str]:
    if not note_path.exists():
        return []
    lines = note_path.read_text(encoding="utf-8").splitlines()
    capture = False
    items: list[str] = []
    for line in lines:
        if line.startswith("## Today's Plan"):
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture and line.lstrip().startswith("- ["):
            items.append(line.strip())
    return items[:3]


def summarize_markdown(path: Path | None, limit: int = 6) -> list[str]:
    if path is None or not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[:limit]


def count_open_rows(path: Path) -> int:
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and "| open |" in line:
            count += 1
    return count


def overnight_actions_summary() -> str:
    """Query recent overnight auto-approved events for morning report."""
    import sqlite3
    db_path = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) / "runtime" / "rick-runtime.db"
    if not db_path.exists():
        return "No runtime DB found."

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT event_type, payload_json, created_at
            FROM events
            WHERE event_type IN ('overnight_auto_approve', 'overnight_auto_approved', 'job_done', 'job_failed')
            AND created_at >= datetime('now', '-12 hours')
            ORDER BY created_at DESC
            LIMIT 20
            """,
        ).fetchall()
        conn.close()
    except Exception:
        return "Could not query overnight events."

    if not rows:
        return "No overnight activity in the last 12 hours."

    lines = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            payload = {}
        event = row["event_type"]
        time = row["created_at"]
        if event in ("overnight_auto_approve", "overnight_auto_approved"):
            area = payload.get("area", "unknown")
            lines.append(f"- [{time}] AUTO-APPROVED: {area}")
        elif event == "job_done":
            lines.append(f"- [{time}] Completed: {payload.get('step_name', '?')} — {payload.get('summary', '')[:80]}")
        elif event == "job_failed":
            lines.append(f"- [{time}] FAILED: {payload.get('error', '')[:80]}")

    return "\n".join(lines) if lines else "No notable overnight events."


def main() -> None:
    brief_date = date.today() + timedelta(days=1)
    brief_dir = DATA_ROOT / "control" / "morning-briefs"
    brief_dir.mkdir(parents=True, exist_ok=True)

    today_note = DATA_ROOT / "memory" / f"{date.today():%Y-%m-%d}.md"
    latest_revenue = latest_file(DATA_ROOT / "revenue", "*.md")
    latest_deps = DATA_ROOT / "control" / "dependency-gaps.md"
    approvals = DATA_ROOT / "control" / "approvals.md"

    plan_items = extract_plan_items(today_note)
    revenue_lines = summarize_markdown(latest_revenue, limit=8)
    dependency_lines = summarize_markdown(latest_deps, limit=8)
    open_approvals = count_open_rows(approvals)

    output = [
        f"# Morning Brief — {brief_date:%Y-%m-%d}",
        "",
        "## Revenue Snapshot",
    ]

    if revenue_lines:
        output.extend(revenue_lines)
    else:
        output.append("- No revenue snapshot found yet.")

    output.extend([
        "",
        "## Top 3 Priorities",
    ])
    output.extend(plan_items or ["- [ ] No plan items found in today's note."])

    output.extend([
        "",
        "## Open Approvals",
        f"- {open_approvals} open approval item(s).",
        "",
        "## Dependency Gaps",
    ])
    output.extend(dependency_lines or ["- No dependency report found yet."])

    output.extend([
        "",
        "## Overnight Activity",
        "",
    ])
    output.append(overnight_actions_summary())

    output_path = brief_dir / f"{brief_date:%Y-%m-%d}.md"
    output_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
