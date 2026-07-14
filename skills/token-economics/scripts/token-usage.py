#!/usr/bin/env python3
"""Record and summarize Rick's LLM usage and budget pressure."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
USAGE_FILE = Path(
    os.path.expanduser(os.getenv("RICK_LLM_USAGE_LOG_FILE", str(DATA_ROOT / "operations" / "llm-usage.jsonl")))
)
TOKEN_BUDGET_FILE = Path(
    os.path.expanduser(os.getenv("RICK_TOKEN_BUDGET_FILE", str(DATA_ROOT / "config" / "token-budgets.json")))
)
DASHBOARD_FILE = DATA_ROOT / "dashboards" / "token-economics.md"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def load_budget_caps() -> dict[str, float]:
    if not TOKEN_BUDGET_FILE.exists():
        return {}
    try:
        payload = json.loads(TOKEN_BUDGET_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    caps = payload.get("daily_usd_caps", {})
    if not isinstance(caps, dict):
        return {}
    normalized: dict[str, float] = {}
    for key, value in caps.items():
        try:
            normalized[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return normalized


def load_events(days: int | None = None) -> list[dict]:
    if not USAGE_FILE.exists():
        return []

    cutoff = datetime.now() - timedelta(days=days) if days is not None else None
    events: list[dict] = []
    for raw_line in USAGE_FILE.read_text(encoding="utf-8").splitlines():
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
    ensure_parent(USAGE_FILE)
    with USAGE_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def record(args: argparse.Namespace) -> int:
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "bucket": args.bucket,
        "provider": args.provider,
        "model": args.model,
        "usd": round(float(args.usd), 4),
        "input_tokens": int(args.input_tokens),
        "output_tokens": int(args.output_tokens),
        "task": args.task,
        "project": args.project,
        "status": args.status,
    }
    append_event(payload)
    print(json.dumps(payload, indent=2))
    return 0


def render_report(days: int) -> str:
    budgets = load_budget_caps()
    events = load_events(days=days)
    today = datetime.now().date()
    today_events = [
        event
        for event in load_events(days=2)
        if parse_timestamp(event.get("timestamp")) and parse_timestamp(event.get("timestamp")).date() == today
    ]

    lines = [
        "# Token Economics",
        "",
        f"- Window: last {days} days",
        f"- Today spend: ${sum(float(event.get('usd', 0)) for event in today_events):.2f}",
        f"- Last {days}d spend: ${sum(float(event.get('usd', 0)) for event in events):.2f}",
        f"- Last {days}d calls: {len(events)}",
        "",
    ]

    if not events:
        lines.extend(
            [
                "No LLM usage recorded yet.",
                "",
                "Use `python3 skills/token-economics/scripts/token-usage.py record ...` after meaningful model runs.",
            ]
        )
        return "\n".join(lines)

    by_bucket: dict[str, dict[str, float]] = defaultdict(lambda: {"usd": 0.0, "tokens": 0.0, "calls": 0.0})
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: {"usd": 0.0, "tokens": 0.0, "calls": 0.0})

    for event in events:
        bucket = str(event.get("bucket", "unknown"))
        model = str(event.get("model", "unknown"))
        usd = float(event.get("usd", 0) or 0)
        tokens = int(event.get("input_tokens", 0) or 0) + int(event.get("output_tokens", 0) or 0)
        by_bucket[bucket]["usd"] += usd
        by_bucket[bucket]["tokens"] += tokens
        by_bucket[bucket]["calls"] += 1
        by_model[model]["usd"] += usd
        by_model[model]["tokens"] += tokens
        by_model[model]["calls"] += 1

    today_bucket_spend: dict[str, float] = defaultdict(float)
    for event in today_events:
        today_bucket_spend[str(event.get("bucket", "unknown"))] += float(event.get("usd", 0) or 0)

    lines.extend(
        [
            "## Budget Pressure",
            "",
            "| Bucket | Today | Daily Cap | Status |",
            "|--------|-------|-----------|--------|",
        ]
    )
    for bucket in sorted(set(list(by_bucket.keys()) + list(budgets.keys()))):
        spend = today_bucket_spend.get(bucket, 0.0)
        cap = budgets.get(bucket)
        if cap is None:
            status = "no cap"
            cap_label = "-"
        else:
            cap_label = f"${cap:.2f}"
            status = "over cap" if spend > cap else "ok"
        lines.append(f"| {bucket} | ${spend:.2f} | {cap_label} | {status} |")

    lines.extend(
        [
            "",
            "## Spend By Bucket",
            "",
            "| Bucket | Spend | Tokens | Calls |",
            "|--------|-------|--------|-------|",
        ]
    )
    for bucket, stats in sorted(by_bucket.items(), key=lambda item: item[1]["usd"], reverse=True):
        lines.append(
            f"| {bucket} | ${stats['usd']:.2f} | {int(stats['tokens'])} | {int(stats['calls'])} |"
        )

    lines.extend(
        [
            "",
            "## Spend By Model",
            "",
            "| Model | Spend | Tokens | Calls |",
            "|-------|-------|--------|-------|",
        ]
    )
    for model, stats in sorted(by_model.items(), key=lambda item: item[1]["usd"], reverse=True)[:10]:
        lines.append(
            f"| {model} | ${stats['usd']:.2f} | {int(stats['tokens'])} | {int(stats['calls'])} |"
        )

    return "\n".join(lines)


def report(args: argparse.Namespace) -> int:
    body = render_report(args.days)
    print(body)
    if args.write:
        ensure_parent(DASHBOARD_FILE)
        DASHBOARD_FILE.write_text(body + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record and summarize Rick LLM usage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record", help="Append an LLM usage event")
    record_parser.add_argument("--bucket", required=True, help="heartbeat, strategic, coding, research, workhorse")
    record_parser.add_argument("--provider", required=True, help="openai, anthropic, google, xai")
    record_parser.add_argument("--model", required=True, help="Exact model id used")
    record_parser.add_argument("--usd", required=True, type=float, help="USD cost for the run")
    record_parser.add_argument("--input-tokens", default=0, type=int, help="Prompt or input tokens")
    record_parser.add_argument("--output-tokens", default=0, type=int, help="Completion or output tokens")
    record_parser.add_argument("--task", default="", help="Task or run description")
    record_parser.add_argument("--project", default="", help="Project slug")
    record_parser.add_argument("--status", default="done", help="done, failed, cancelled")
    record_parser.set_defaults(func=record)

    report_parser = subparsers.add_parser("report", help="Render a markdown spend report")
    report_parser.add_argument("--days", type=int, default=14, help="Lookback window in days")
    report_parser.add_argument("--write", action="store_true", help="Write the report dashboard")
    report_parser.set_defaults(func=report)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
