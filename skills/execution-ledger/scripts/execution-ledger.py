#!/usr/bin/env python3
"""Record and summarize Rick's execution events."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEDGER_FILE = Path(
    os.path.expanduser(
        os.getenv("RICK_EXECUTION_LEDGER_FILE", str(DATA_ROOT / "operations" / "execution-ledger.jsonl"))
    )
)
DASHBOARD_FILE = DATA_ROOT / "dashboards" / "execution-ledger.md"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def load_events(days: int | None = None) -> list[dict]:
    if not LEDGER_FILE.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days) if days is not None else None
    events: list[dict] = []
    for raw_line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
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
        events.append(payload)
    return events


def append_event(payload: dict) -> None:
    ensure_parent(LEDGER_FILE)
    with LEDGER_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def record(args: argparse.Namespace) -> int:
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "kind": args.kind,
        "title": args.title,
        "status": args.status,
        "area": args.area,
        "project": args.project,
        "route": args.route,
        "impact": args.impact,
        "notes": args.notes,
        "artifacts": args.artifact or [],
    }
    append_event(payload)
    print(json.dumps(payload, indent=2))
    return 0


def render_summary(days: int) -> str:
    events = load_events(days=days)
    lines = [
        "# Execution Ledger",
        "",
        f"- Window: last {days} days",
        f"- Total events: {len(events)}",
        "",
    ]

    if not events:
        lines.extend(
            [
                "No execution events recorded yet.",
                "",
                "Use `python3 skills/execution-ledger/scripts/execution-ledger.py record ...` to start the ledger.",
            ]
        )
        return "\n".join(lines)

    status_counts = Counter(event.get("status", "unknown") for event in events)
    kind_counts = Counter(event.get("kind", "unknown") for event in events)
    area_counts = Counter(event.get("area", "unknown") for event in events)

    lines.extend(
        [
            "## Status Counts",
            "",
            "| Status | Count |",
            "|--------|-------|",
        ]
    )
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(
        [
            "",
            "## Kind Counts",
            "",
            "| Kind | Count |",
            "|------|-------|",
        ]
    )
    for kind, count in sorted(kind_counts.items()):
        lines.append(f"| {kind} | {count} |")

    lines.extend(
        [
            "",
            "## Area Counts",
            "",
            "| Area | Count |",
            "|------|-------|",
        ]
    )
    for area, count in sorted(area_counts.items()):
        lines.append(f"| {area} | {count} |")

    lines.extend(
        [
            "",
            "## Latest Events",
            "",
            "| Timestamp | Kind | Status | Title | Project |",
            "|-----------|------|--------|-------|---------|",
        ]
    )
    for event in events[-10:][::-1]:
        lines.append(
            "| {timestamp} | {kind} | {status} | {title} | {project} |".format(
                timestamp=event.get("timestamp", ""),
                kind=event.get("kind", ""),
                status=event.get("status", ""),
                title=event.get("title", ""),
                project=event.get("project", ""),
            )
        )

    return "\n".join(lines)


def summary(args: argparse.Namespace) -> int:
    body = render_summary(args.days)
    print(body)
    if args.write:
        ensure_parent(DASHBOARD_FILE)
        DASHBOARD_FILE.write_text(body + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and summarize Rick execution events")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Append an execution event")
    record_parser.add_argument("--kind", required=True, help="decision, ship, blocker, approval, system-run, experiment")
    record_parser.add_argument("--title", required=True, help="Short event title")
    record_parser.add_argument("--status", default="done", help="done, open, blocked, dropped")
    record_parser.add_argument("--area", default="operations", help="Operating area")
    record_parser.add_argument("--project", default="", help="Project or initiative slug")
    record_parser.add_argument("--route", default="", help="Model or workflow route used")
    record_parser.add_argument("--impact", default="", help="Expected or actual impact")
    record_parser.add_argument("--notes", default="", help="Human-readable context")
    record_parser.add_argument("--artifact", action="append", default=[], help="Artifact path or URL")
    record_parser.set_defaults(func=record)

    summary_parser = subparsers.add_parser("summary", help="Render a markdown summary")
    summary_parser.add_argument("--days", type=int, default=14, help="Lookback window in days")
    summary_parser.add_argument("--write", action="store_true", help="Write the summary dashboard")
    summary_parser.set_defaults(func=summary)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
