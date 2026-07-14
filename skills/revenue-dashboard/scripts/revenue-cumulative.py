#!/usr/bin/env python3
"""Aggregate daily revenue snapshots into a cumulative tracker."""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
REVENUE_DIR = DATA_ROOT / "revenue"
CUMULATIVE_FILE = REVENUE_DIR / "cumulative.json"
SUMMARY_FILE = REVENUE_DIR / "SUMMARY.md"

DEFAULT_TARGETS = {
    "partner-connector": 30000,
    "404-agency": 5000,
    "personal-brand": 15000,
    "info-products": 40000,
    "lingualive": 10000,
}


def load_targets() -> dict:
    portfolio_file = os.getenv("RICK_PORTFOLIO_FILE", "").strip()
    if portfolio_file:
        resolved = Path(os.path.expanduser(portfolio_file))
        if resolved.exists():
            raw = json.loads(resolved.read_text(encoding="utf-8"))
            products = raw.get("products", {})
            return {name: int(payload.get("target_mrr", 0)) for name, payload in products.items()}
    return DEFAULT_TARGETS.copy()


def parse_snapshot(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
        return None
    net_match = re.search(r"\|\s*Period Net Revenue\s*\|\s*\$?([\d,.\-]+)", text)
    count_match = re.search(r"\|\s*Transactions\s*\|\s*(\d+)", text)
    period_match = re.search(r"\*\*Period:\*\*\s*(.+)", text)
    if not net_match:
        return None
    net_str = net_match.group(1).replace(",", "").replace("-", "")
    is_negative = "-" in net_match.group(1)
    net = float(net_str) * (-1 if is_negative else 1)
    return {
        "date": path.stem,
        "net": net,
        "count": int(count_match.group(1)) if count_match else 0,
        "period": period_match.group(1).strip() if period_match else "unknown",
    }


def build_cumulative() -> dict:
    snapshots = sorted(REVENUE_DIR.glob("20*.md"))
    entries = []
    running_total = 0.0
    for path in snapshots:
        parsed = parse_snapshot(path)
        if parsed is None:
            continue
        running_total += parsed["net"]
        entries.append({
            "date": parsed["date"],
            "daily_net": parsed["net"],
            "cumulative": round(running_total, 2),
            "transactions": parsed["count"],
        })
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "snapshot_count": len(entries),
        "cumulative_revenue": round(running_total, 2),
        "entries": entries,
    }


def build_summary(cumulative: dict) -> str:
    targets = load_targets()
    total_target = sum(targets.values())
    entries = cumulative["entries"]
    lines = [
        "# Revenue Summary",
        "",
        f"**Generated:** {cumulative['generated']}",
        f"**Snapshots tracked:** {cumulative['snapshot_count']}",
        f"**Cumulative revenue:** ${cumulative['cumulative_revenue']:,.2f}",
        f"**Monthly target:** ${total_target:,.2f}",
        "",
    ]

    if entries:
        lines.extend([
            "## Recent Snapshots",
            "",
            "| Date | Daily Net | Cumulative | Txns |",
            "|------|-----------|------------|------|",
        ])
        for entry in entries[-14:]:
            lines.append(
                f"| {entry['date']} | ${entry['daily_net']:,.2f} | ${entry['cumulative']:,.2f} | {entry['transactions']} |"
            )
    else:
        lines.append("No revenue snapshots recorded yet. Run `revenue-report.py --period yesterday` or wait for the nightly cron.")

    lines.extend([
        "",
        "## Proof Loop Status",
        "",
    ])

    if cumulative["snapshot_count"] == 0:
        lines.append("- Revenue tracking: NOT STARTED — no snapshots written")
        lines.append("- Action: Configure Stripe accounts in `config/stripe-accounts.json` and run nightly")
    elif cumulative["cumulative_revenue"] == 0:
        lines.append("- Revenue tracking: ACTIVE — snapshots exist but $0 revenue recorded")
        lines.append("- Action: Launch a product with a payment path")
    else:
        lines.append(f"- Revenue tracking: ACTIVE")
        lines.append(f"- First snapshot: {entries[0]['date']}")
        lines.append(f"- Latest snapshot: {entries[-1]['date']}")
        gap = total_target - cumulative["cumulative_revenue"]
        lines.append(f"- Gap to monthly target: ${gap:,.2f}")

    return "\n".join(lines) + "\n"


def main():
    REVENUE_DIR.mkdir(parents=True, exist_ok=True)
    cumulative = build_cumulative()
    CUMULATIVE_FILE.write_text(json.dumps(cumulative, indent=2) + "\n", encoding="utf-8")
    print(f"Written cumulative data to {CUMULATIVE_FILE}")

    summary = build_summary(cumulative)
    SUMMARY_FILE.write_text(summary, encoding="utf-8")
    print(f"Written summary to {SUMMARY_FILE}")
    print(summary)


if __name__ == "__main__":
    main()
