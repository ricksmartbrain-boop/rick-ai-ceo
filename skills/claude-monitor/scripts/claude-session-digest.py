#!/usr/bin/env python3
"""Daily digest of Claude Code sessions from ~/.claude/history.jsonl."""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", Path.home() / "rick-vault"))
HISTORY_FILE = Path.home() / ".claude" / "history.jsonl"
OUTPUT_FILE = DATA_ROOT / "dashboards" / "claude-sessions.md"

WINDOW_HOURS = 24


def ensure_parent(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def load_history(cutoff_epoch_ms: int) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    entries = []
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("timestamp", 0)
        if ts >= cutoff_epoch_ms:
            entries.append(entry)
    return entries


def build_digest(entries: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    if not entries:
        return (
            f"---\nupdated: {now_str}\ntype: claude-sessions\n---\n\n"
            f"# Claude Sessions (last {WINDOW_HOURS}h)\n\nNo sessions found.\n"
        )

    sessions: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        sid = e.get("sessionId", "unknown")
        sessions[sid].append(e)

    projects_touched: set[str] = set()
    rows = []
    for sid, events in sorted(
        sessions.items(),
        key=lambda kv: min(e.get("timestamp", 0) for e in kv[1]),
    ):
        first_ts = min(e.get("timestamp", 0) for e in events)
        last_ts = max(e.get("timestamp", 0) for e in events)
        start = datetime.fromtimestamp(first_ts / 1000, tz=timezone.utc).strftime(
            "%H:%M"
        )
        end = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).strftime("%H:%M")
        project = events[0].get("project", "—")
        if project and project != "—":
            projects_touched.add(project)
        display = events[0].get("display", sid[:12])
        rows.append(f"| {start}–{end} | {display} | {project} | {len(events)} |")

    header = (
        f"---\nupdated: {now_str}\ntype: claude-sessions\n---\n\n"
        f"# Claude Sessions (last {WINDOW_HOURS}h)\n\n"
        f"- **Sessions:** {len(sessions)}\n"
        f"- **Projects touched:** {len(projects_touched)}\n\n"
        f"| Time (UTC) | Display | Project | Events |\n"
        f"|------------|---------|---------|--------|\n"
    )
    return header + "\n".join(rows) + "\n"


def main() -> None:
    cutoff_ms = int((time.time() - WINDOW_HOURS * 3600) * 1000)
    entries = load_history(cutoff_ms)
    md = build_digest(entries)
    ensure_parent(OUTPUT_FILE)
    OUTPUT_FILE.write_text(md)
    print(f"claude-session-digest: wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
