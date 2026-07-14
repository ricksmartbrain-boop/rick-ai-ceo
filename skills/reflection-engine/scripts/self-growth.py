#!/usr/bin/env python3
"""Extract repeated failure patterns and wins from past retros and execution ledger."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# Allow imports from project root
ROOT_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT_DIR))

from runtime.llm import generate_text  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))


def _read_if_exists(path: Path) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def _collect_daily_retros(days: int = 7) -> str:
    retro_dir = DATA_ROOT / "reflections" / "daily"
    today = date.today()
    parts: list[str] = []
    for i in range(days):
        d = today - timedelta(days=i + 1)
        content = _read_if_exists(retro_dir / f"{d:%Y-%m-%d}.md")
        if content.strip():
            parts.append(content)
    return "\n---\n".join(parts)


def _collect_weekly_retros(count: int = 2) -> str:
    retro_dir = DATA_ROOT / "reflections" / "weekly"
    if not retro_dir.is_dir():
        return ""
    files = sorted(retro_dir.glob("*.md"), reverse=True)
    parts: list[str] = []
    for f in files[:count]:
        content = f.read_text(encoding="utf-8")
        if content.strip():
            parts.append(content)
    return "\n---\n".join(parts)


def _collect_ledger_entries(days: int = 7) -> str:
    ledger_path = Path(
        os.path.expanduser(
            os.getenv(
                "RICK_EXECUTION_LEDGER_FILE",
                str(DATA_ROOT / "operations" / "execution-ledger.jsonl"),
            )
        )
    )
    if not ledger_path.is_file():
        return ""
    cutoff = date.today() - timedelta(days=days)
    entries: list[str] = []
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = entry.get("timestamp", "")[:10]
        if ts >= cutoff.isoformat():
            entries.append(f"- [{entry.get('kind', '?')}] {entry.get('title', '?')} → {entry.get('status', '?')}")
    return "\n".join(entries[-100:])  # cap at 100 recent


def _build_prompt(daily: str, weekly: str, ledger: str) -> str:
    return f"""Analyse Rick's past week of operations. Extract:

1. **repeated_failure_patterns**: patterns that appeared 2+ times (title, frequency, root cause hypothesis)
2. **wins**: things that worked well (title, impact)
3. **corrective_actions**: concrete changes to make (action, rationale, priority high/medium/low)
4. **queued_initiatives**: new workflow ideas that address failures or amplify wins (title, kind, rationale) — max 3

Return ONLY valid JSON with those four top-level keys. No markdown fences, no commentary.

--- DAILY RETROS ---
{daily or '(none)'}

--- WEEKLY RETROS ---
{weekly or '(none)'}

--- EXECUTION LEDGER (7 days) ---
{ledger or '(none)'}"""


def main() -> None:
    daily = _collect_daily_retros()
    weekly = _collect_weekly_retros()
    ledger = _collect_ledger_entries()

    if not daily and not weekly and not ledger:
        print("No retro or ledger data found. Skipping self-growth analysis.")
        return

    prompt = _build_prompt(daily, weekly, ledger)
    result = generate_text("analysis", prompt, fallback="{}")

    # Parse the LLM output
    text = result.content.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = "\n".join(text.split("\n")[:-1])

    try:
        learnings = json.loads(text)
    except json.JSONDecodeError:
        print(f"WARN: LLM returned non-JSON, saving raw text. Preview: {text[:200]}")
        learnings = {"raw": text}

    # Write JSON output
    output_dir = DATA_ROOT / "reflections" / "learnings"
    output_dir.mkdir(parents=True, exist_ok=True)

    latest_path = output_dir / "latest.json"
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(json.dumps(learnings, indent=2, ensure_ascii=False), encoding="utf-8")

    # Auto-queue top initiatives (cap at 2 per run)
    initiatives = learnings.get("queued_initiatives", [])
    for initiative in initiatives[:2]:
        objective = initiative if isinstance(initiative, str) else initiative.get("objective", str(initiative))
        if not objective:
            continue
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT_DIR / "runtime" / "runner.py"),
                 "queue-initiative", "--objective", objective[:200]],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                print(f"  Queued initiative: {objective[:80]}")
            else:
                print(f"  Failed to queue initiative: {result.stderr[:200]}")
        except Exception as exc:
            print(f"  Error queuing initiative: {exc}")

    # Write markdown summary
    today = date.today()
    md_path = output_dir / f"{today:%Y-%m-%d}.md"
    md_lines = [
        f"---\ntype: self-growth-learnings\ndate: {today:%Y-%m-%d}\n---\n",
        f"# Self-Growth Learnings — {today:%Y-%m-%d}\n",
    ]

    for section, heading in [
        ("repeated_failure_patterns", "Repeated Failure Patterns"),
        ("wins", "Wins"),
        ("corrective_actions", "Corrective Actions"),
        ("queued_initiatives", "Queued Initiatives"),
    ]:
        md_lines.append(f"\n## {heading}\n")
        items = learnings.get(section, [])
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    md_lines.append(f"- **{item.get('title', item.get('action', '?'))}**: {json.dumps(item)}")
                else:
                    md_lines.append(f"- {item}")
        else:
            md_lines.append(f"{items}")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"Learnings written to {latest_path}")
    print(f"Markdown written to {md_path}")


if __name__ == "__main__":
    main()
