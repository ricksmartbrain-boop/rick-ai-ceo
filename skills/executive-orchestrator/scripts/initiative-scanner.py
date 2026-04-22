#!/usr/bin/env python3
"""Rule-based initiative scanner. No LLM calls — reads portfolio, learnings, and runtime DB."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from runtime.db import connect  # noqa: E402
from runtime.engine import create_workflow, queue_job, WORKFLOW_STEP_MAP  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
ROOT_DIR = Path(__file__).resolve().parents[3]
MAX_NEW_INITIATIVES = 3


def _load_json(path: Path) -> dict | list | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _portfolio_path() -> Path:
    return Path(
        os.path.expanduser(
            os.getenv("RICK_PORTFOLIO_FILE", str(ROOT_DIR / "config" / "portfolio.json"))
        )
    )


def _stalled_workflows(conn) -> list[dict]:
    """Find active/blocked workflows stalled 7+ days."""
    cutoff = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT id, kind, title, project, status, updated_at
        FROM workflows
        WHERE status IN ('active', 'blocked')
          AND updated_at < ?
        ORDER BY updated_at ASC
        LIMIT 10
        """,
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _existing_initiative_titles(conn) -> set[str]:
    """Get titles of existing initiative workflows to avoid duplicates."""
    rows = conn.execute(
        "SELECT title FROM workflows WHERE kind = 'initiative' AND status NOT IN ('done', 'cancelled')"
    ).fetchall()
    return {r["title"] for r in rows}


def _last_artifact_age_hours(conn) -> float | None:
    """Hours since last artifact was recorded in execution ledger."""
    ledger_path = Path(
        os.path.expanduser(
            os.getenv(
                "RICK_EXECUTION_LEDGER_FILE",
                str(DATA_ROOT / "operations" / "execution-ledger.jsonl"),
            )
        )
    )
    if not ledger_path.is_file():
        return None
    last_ts = None
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("artifacts"):
            ts_str = entry.get("timestamp", "")
            if ts_str:
                try:
                    last_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    pass
    if last_ts is None:
        return None
    delta = datetime.now().astimezone() - last_ts if last_ts.tzinfo else datetime.now() - last_ts
    return delta.total_seconds() / 3600


def _queue_initiative(conn, title: str, rationale: str, existing: set[str]) -> bool:
    """Create an initiative workflow if not duplicate. Returns True if created."""
    if title in existing:
        return False
    slug = title.lower().replace(" ", "-")[:40]
    context = {
        "product_slug": f"initiative-{slug}",
        "rationale": rationale,
    }
    wf_id = create_workflow(conn, "initiative", title, "rick-v6", context, priority=40, lane="ops-lane")
    # Queue the first step
    steps = WORKFLOW_STEP_MAP.get("initiative", [])
    if steps:
        step_name, route = steps[0]
        queue_job(conn, wf_id, step_name, 0, route, f"{title} — {step_name}")
    print(f"  Queued initiative: {title} ({wf_id})")
    existing.add(title)
    return True


def scan(conn) -> int:
    """Run all rules. Returns count of new initiatives queued."""
    if os.getenv("RICK_INITIATIVE_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        print("Initiative scanner: DISABLED via RICK_INITIATIVE_DISABLED — no scan.")
        return 0
    existing = _existing_initiative_titles(conn)
    queued = 0

    # Rule 1: Stalled workflows → unblock initiatives
    if queued < MAX_NEW_INITIATIVES:
        stalled = _stalled_workflows(conn)
        for wf in stalled:
            if queued >= MAX_NEW_INITIATIVES:
                break
            title = f"Unblock: {wf['title']}"
            rationale = f"Workflow {wf['id']} ({wf['kind']}) stalled since {wf['updated_at']}"
            if _queue_initiative(conn, title, rationale, existing):
                queued += 1

    # Rule 2: Learnings bridge — create workflows from queued_initiatives in latest.json
    if queued < MAX_NEW_INITIATIVES:
        learnings = _load_json(DATA_ROOT / "reflections" / "learnings" / "latest.json")
        if isinstance(learnings, dict):
            for item in learnings.get("queued_initiatives", []):
                if queued >= MAX_NEW_INITIATIVES:
                    break
                if isinstance(item, dict):
                    title = item.get("title", "")
                    rationale = item.get("rationale", "from self-growth learnings")
                else:
                    title = str(item)
                    rationale = "from self-growth learnings"
                if title and _queue_initiative(conn, title, rationale, existing):
                    queued += 1

    # Rule 3: Shipping cadence — no artifact in 72+ hours
    if queued < MAX_NEW_INITIATIVES:
        age = _last_artifact_age_hours(conn)
        if age is not None and age > 72:
            title = "Ship something: shipping cadence stalled"
            rationale = f"No artifact recorded in {age:.0f} hours (threshold: 72h)"
            if _queue_initiative(conn, title, rationale, existing):
                queued += 1

    return queued


def main() -> None:
    conn = connect()
    try:
        count = scan(conn)
        conn.commit()
        print(f"Initiative scanner: {count} new initiative(s) queued.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
