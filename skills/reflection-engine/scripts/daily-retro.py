#!/usr/bin/env python3
"""Create a daily reflection shell for Rick, auto-populated from execution ledger."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEDGER_PATH = DATA_ROOT / "operations" / "execution-ledger.jsonl"
LEDGER_TAIL_LINES = 300


def _target_date() -> date:
    """Return today, or yesterday if running before 5am (e.g. 3am cron)."""
    now = datetime.now()
    if now.hour < 5:
        return (now - timedelta(days=1)).date()
    return now.date()


def _read_ledger_entries(target: date) -> dict[str, list[dict]]:
    """Read last N lines of the execution ledger and bucket by status."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    if not LEDGER_PATH.exists():
        return buckets

    # Read last LEDGER_TAIL_LINES lines efficiently
    lines: list[str] = []
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as fh:
            all_lines = fh.readlines()
            lines = all_lines[-LEDGER_TAIL_LINES:]
    except OSError:
        return buckets

    target_str = target.isoformat()  # "YYYY-MM-DD"

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        # Match entries whose timestamp or date field falls on the target date
        entry_date = None
        for key in ("timestamp", "date", "ts", "created_at"):
            val = entry.get(key, "")
            if isinstance(val, str) and val.strip()[:10] == target_str:
                entry_date = target_str
                break
        if entry_date is None:
            continue

        status = str(entry.get("status", "unknown")).lower()
        if status in ("done", "shipped", "completed", "success"):
            buckets["shipped"].append(entry)
        elif status in ("blocked", "stalled", "waiting", "pending"):
            buckets["stalled"].append(entry)
        elif status in ("failed", "error", "errored", "broke"):
            buckets["broke"].append(entry)

    return buckets


def _format_shipped(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        kind = e.get("kind", e.get("type", "task"))
        title = e.get("title", e.get("name", e.get("description", "untitled")))
        route = e.get("route", e.get("lane", ""))
        suffix = f" ({route})" if route else ""
        lines.append(f"- [{kind}] {title}{suffix}")
    return "\n".join(lines) if lines else ""


def _format_stalled(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        kind = e.get("kind", e.get("type", "task"))
        title = e.get("title", e.get("name", e.get("description", "untitled")))
        reason = e.get("reason", e.get("blocked_by", e.get("note", "")))
        suffix = f" — {reason}" if reason else ""
        lines.append(f"- [{kind}] {title}{suffix}")
    return "\n".join(lines) if lines else ""


def _format_broke(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        kind = e.get("kind", e.get("type", "task"))
        title = e.get("title", e.get("name", e.get("description", "untitled")))
        error = e.get("error", e.get("reason", e.get("message", "")))
        suffix = f" — {error}" if error else ""
        lines.append(f"- [{kind}] {title}{suffix}")
    return "\n".join(lines) if lines else ""


def main() -> None:
    output_dir = DATA_ROOT / "reflections" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)

    target = _target_date()
    output_path = output_dir / f"{target:%Y-%m-%d}.md"

    # Auto-populate from execution ledger
    buckets = _read_ledger_entries(target)
    total_entries = sum(len(v) for v in buckets.values())
    shipped_text = _format_shipped(buckets.get("shipped", []))
    stalled_text = _format_stalled(buckets.get("stalled", []))
    broke_text = _format_broke(buckets.get("broke", []))

    if total_entries == 0:
        print(f"[daily-retro] No ledger entries matched {target}; creating empty retro shell.")

    content = f"""---
type: daily-reflection
date: {target:%Y-%m-%d}
entries_matched: {total_entries}
---

# Daily Reflection — {target:%Y-%m-%d}

## What Shipped
{shipped_text}

## What Stalled
{stalled_text}

## Failure Modes
{broke_text}

## Token Spend and Routing Notes

## What To Change Tomorrow

## Auto-captured from execution ledger
### Shipped
{shipped_text if shipped_text else "_No entries found._"}
### Stalled
{stalled_text if stalled_text else "_No entries found._"}
### Failed
{broke_text if broke_text else "_No entries found._"}
"""
    output_path.write_text(content, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
