#!/usr/bin/env python3
"""anthropic-daily-cost.py — daily Anthropic spend extracted from session jsonl logs.

Reads ~/.openclaw/agents/*/sessions/*.jsonl, sums the per-turn `cost.total`
field, partitions by day. Prints last N days table + a one-line digest
suitable for piping into the existing weekly roundup.

Usage:
  python3 scripts/anthropic-daily-cost.py            # last 14 days table
  python3 scripts/anthropic-daily-cost.py --days 30  # last 30 days
  python3 scripts/anthropic-daily-cost.py --digest   # one-line for cron pipe
  python3 scripts/anthropic-daily-cost.py --json     # JSON output
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from collections import defaultdict
from pathlib import Path

DATE_RE = re.compile(r'"ts":"(2026-\d{2}-\d{2})')
TOTAL_RE = re.compile(r'"cost":\{[^}]*"total":([0-9.e-]+)')
MODEL_RE = re.compile(r'"model":"([^"]+)"')
TOKENS_RE = re.compile(r'"totalTokens":(\d+)')


def collect() -> dict:
    """Walk all session jsonl files; bucket per-turn costs by day."""
    totals: dict[str, dict] = defaultdict(
        lambda: {"cost": 0.0, "turns": 0, "opus": 0, "sonnet": 0, "tokens": 0}
    )
    sessions_glob = os.path.expanduser(
        "~/.openclaw/agents/*/sessions/*.jsonl"
    )
    for path in glob.glob(sessions_glob):
        if ".deleted." in path:
            continue
        try:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    d = DATE_RE.search(line)
                    t = TOTAL_RE.search(line)
                    if not (d and t):
                        continue
                    day = d.group(1)
                    totals[day]["cost"] += float(t.group(1))
                    totals[day]["turns"] += 1
                    m = MODEL_RE.search(line)
                    if m:
                        if "opus" in m.group(1).lower():
                            totals[day]["opus"] += 1
                        elif "sonnet" in m.group(1).lower():
                            totals[day]["sonnet"] += 1
                    tok = TOKENS_RE.search(line)
                    if tok:
                        totals[day]["tokens"] += int(tok.group(1))
        except OSError:
            continue
    return dict(totals)


def render_table(totals: dict, days: int) -> str:
    keys = sorted(totals.keys())[-days:]
    lines = [f'{"date":12} {"$":>8} {"turns":>7} {"opus":>5} {"opus%":>6} {"k tok":>8}']
    for d in keys:
        t = totals[d]
        pct = (t["opus"] / t["turns"] * 100) if t["turns"] else 0
        lines.append(
            f'{d:12} ${t["cost"]:>7.2f} {t["turns"]:>7} {t["opus"]:>5} {pct:>5.0f}% {t["tokens"]/1000:>7.0f}k'
        )
    total = sum(t["cost"] for d, t in totals.items() if d in keys)
    lines.append(f'{"window total":12} ${total:>7.2f}')
    return "\n".join(lines)


def render_digest(totals: dict) -> str:
    """One-line digest for the weekly roundup."""
    keys = sorted(totals.keys())
    if not keys:
        return "anthropic spend: no data"
    today = keys[-1]
    t = totals[today]
    last_7 = sum(totals[d]["cost"] for d in keys[-7:])
    return (
        f"anthropic ${t['cost']:.2f} today ({t['turns']} turns, {t['opus']} opus) "
        f"| 7d: ${last_7:.2f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--digest", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    totals = collect()
    if not totals:
        print("no session jsonl data found")
        return 1
    if args.digest:
        print(render_digest(totals))
    elif args.json:
        out = {d: totals[d] for d in sorted(totals.keys())[-args.days :]}
        print(json.dumps(out, indent=2))
    else:
        print(render_table(totals, args.days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
