#!/usr/bin/env python3
"""Create a weekly reflection shell for Rick."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))


def iso_week_label(today: date) -> str:
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def main() -> None:
    today = date.today()
    output_dir = DATA_ROOT / "reflections" / "weekly"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{iso_week_label(today)}.md"

    content = f"""---
type: weekly-reflection
week: {iso_week_label(today)}
---

# Weekly Reflection — {iso_week_label(today)}

## Biggest Wins

## Biggest Misses

## Repeated Failure Modes

## Token Budget Review

## Playbook Changes
"""
    output_path.write_text(content, encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
