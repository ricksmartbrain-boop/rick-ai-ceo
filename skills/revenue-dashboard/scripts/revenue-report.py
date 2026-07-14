#!/usr/bin/env python3
"""Cross-product revenue report for Rick."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_TARGETS = {
    "partner-connector": 30000,
    "404-agency": 5000,
    "personal-brand": 15000,
    "info-products": 40000,
    "lingualive": 10000,
}
RICK_DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
VAULT_REVENUE_DIR = RICK_DATA_ROOT / "revenue"
ROOT_DIR = Path(__file__).resolve().parents[3]


def load_targets() -> dict:
    portfolio_file = os.getenv("RICK_PORTFOLIO_FILE", "").strip()
    if portfolio_file:
        resolved = Path(os.path.expanduser(portfolio_file))
        if resolved.exists():
            raw = json.loads(resolved.read_text(encoding="utf-8"))
            products = raw.get("products", {})
            return {name: int(payload.get("target_mrr", 0)) for name, payload in products.items()}
    return DEFAULT_TARGETS.copy()


def get_stripe_metrics(period: str) -> dict:
    metrics_script = ROOT_DIR / "skills" / "metrics" / "scripts" / "stripe-metrics.py"
    if not metrics_script.exists():
        return {"error": f"stripe-metrics.py not found at {metrics_script}"}

    try:
        result = subprocess.run(
            ["python3", str(metrics_script), "--period", period, "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            return {"error": result.stderr.strip() or result.stdout.strip() or "metrics command failed"}
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return {"error": str(exc)}


def format_currency(amount: float) -> str:
    return f"${amount:,.2f}" if amount >= 0 else f"-${abs(amount):,.2f}"


def calculate_gap(current: float, target: float) -> dict:
    gap = target - current
    gap_pct = (gap / target * 100) if target else 0
    return {
        "current": current,
        "target": target,
        "gap": gap,
        "gap_pct": round(gap_pct, 1),
        "on_track": current >= target * 0.8 if target else False,
    }


def generate_report(period: str) -> str:
    now = datetime.now()
    metrics = get_stripe_metrics(period)
    targets = load_targets()
    total_target = sum(targets.values())
    report_lines = [
        f"# Revenue Report — {now.strftime('%Y-%m-%d')}",
        f"**Period:** {period}",
        f"**Generated:** {now.strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    if "error" in metrics:
        report_lines.append(f"Warning: {metrics['error']}")
        report_lines.append("")
        metrics = {"_total": {"current": {"net": 0, "count": 0}}}

    total_current = metrics.get("_total", {}).get("current", {})
    total_net = float(total_current.get("net", 0))

    report_lines.extend([
        "## Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Period Net Revenue | {format_currency(total_net)} |",
        f"| Transactions | {int(total_current.get('count', 0))} |",
    ])

    if period == "month":
        gap = calculate_gap(total_net, total_target)
        report_lines.extend([
            f"| Monthly Target | {format_currency(total_target)} |",
            f"| Gap | {format_currency(gap['gap'])} ({gap['gap_pct']}%) |",
            f"| On Track | {'Yes' if gap['on_track'] else 'No'} |",
        ])

    report_lines.extend([
        "",
        "## By Product",
        "",
        "| Product | Net | Growth | Target |",
        "|---------|-----|--------|--------|",
    ])

    for key, payload in metrics.items():
        if key.startswith("_"):
            continue
        product = payload.get("product", key)
        current = payload.get("current", {})
        growth = payload.get("growth_pct", 0)
        target = targets.get(product, 0)
        report_lines.append(
            f"| {product} | {format_currency(float(current.get('net', 0)))} | {growth}% | {format_currency(target) if target else 'n/a'} |"
        )

    report_lines.extend([
        "",
        "## Raw Metrics",
        "",
        f"```json\n{json.dumps(metrics, indent=2)}\n```",
    ])
    return "\n".join(report_lines)


def write_to_vault(report: str, date_str: str):
    VAULT_REVENUE_DIR.mkdir(parents=True, exist_ok=True)
    filepath = VAULT_REVENUE_DIR / f"{date_str}.md"
    frontmatter = f"""---
type: revenue-snapshot
date: {date_str}
created: {datetime.now().isoformat()}
---

"""
    filepath.write_text(frontmatter + report, encoding="utf-8")
    print(f"Written to {filepath}")


def main():
    parser = argparse.ArgumentParser(description="Rick revenue dashboard")
    parser.add_argument("--period", choices=["yesterday", "month", "week", "today"], default="yesterday")
    parser.add_argument("--full", action="store_true", help="Run all standard periods")
    parser.add_argument("--no-write", action="store_true", help="Do not write the report to Rick memory")
    args = parser.parse_args()

    periods = ["yesterday", "week", "month"] if args.full else [args.period]
    last_report = ""
    for period in periods:
        report = generate_report(period)
        last_report = report
        print(report)
        if len(periods) > 1:
            print("\n" + "=" * 60 + "\n")

    if not args.no_write and last_report:
        date_str = (
            (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            if args.period == "yesterday"
            else datetime.now().strftime("%Y-%m-%d")
        )
        write_to_vault(last_report, date_str)


if __name__ == "__main__":
    main()
