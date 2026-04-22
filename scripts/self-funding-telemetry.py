#!/usr/bin/env python3
"""Self-funding telemetry — daily Telegram post showing how much of Rick's
LLM spend is now covered by his own MRR.

Read-only. Computes:
  - Anthropic burn over trailing 30 days (from llm-usage.jsonl)
  - Total LLM burn 30d (all providers)
  - Real MRR (parsed from latest revenue/reconciliation-*.md, fallback $9)
  - Self-funding ratio: (MRR × 12 ÷ 12) / Anthropic_30d
                       (since MRR is monthly, compare to monthly burn directly)
  - Break-even target: $3,900 MRR (≈ 8 Managed customers @ $499)

Posts a single ops-thread message daily at 08:00 with a progress bar +
next milestone. Quiet on weekends if NO_WEEKEND=1.

NEVER touches money — no card actions, no Stripe writes. Pure observability.

Env:
  RICK_SELF_FUNDING_LIVE=1   — actually post to Telegram (default: print only)
  RICK_SELF_FUNDING_TOPIC=ops-alerts  — which topic to post to
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LLM_USAGE = DATA_ROOT / "operations" / "llm-usage.jsonl"
REVENUE_DIR = DATA_ROOT / "revenue"

BREAK_EVEN_MRR = 3900.0  # 100% self-funding target (per swarm Agent A047)

# Hardcoded fallback per SELF-FAQ "Don't surface $547 — that's the phantom"
# Real MRR from sub_1TEGyAD9G3v6e0Osa0sgsrVk only.
FALLBACK_MRR = 9.0


def _trailing_30d_burn() -> tuple[float, float, int]:
    """Returns (anthropic_30d_usd, total_30d_usd, event_count). Tail-only read."""
    if not LLM_USAGE.is_file():
        return (0.0, 0.0, 0)
    cutoff = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    anthropic_usd = 0.0
    total_usd = 0.0
    n = 0
    # Stream-read tail to avoid loading the whole file into memory
    try:
        with LLM_USAGE.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = row.get("timestamp", "")
                if ts < cutoff:
                    continue
                usd = float(row.get("usd") or 0)
                provider = (row.get("provider") or "").lower()
                total_usd += usd
                if provider == "anthropic":
                    anthropic_usd += usd
                n += 1
    except OSError:
        return (0.0, 0.0, 0)
    return (round(anthropic_usd, 2), round(total_usd, 2), n)


def _real_mrr() -> tuple[float, str]:
    """Parse latest reconciliation file for real MRR; fallback to $9."""
    if not REVENUE_DIR.is_dir():
        return (FALLBACK_MRR, "fallback (reconciliation dir absent)")
    recs = sorted(REVENUE_DIR.glob("reconciliation-*.md"))
    if not recs:
        return (FALLBACK_MRR, "fallback (no reconciliation files)")
    latest = recs[-1]
    text = latest.read_text(encoding="utf-8", errors="replace")
    # Pattern: "**Real current MRR:** **$X.XX**" — allow whitespace + asterisks between label and value
    m = re.search(r"Real current MRR[:\*\s]+\$?([0-9]+(?:\.[0-9]+)?)", text)
    if m:
        return (float(m.group(1)), latest.name)
    return (FALLBACK_MRR, f"fallback (couldn't parse {latest.name})")


def _progress_bar(ratio: float, width: int = 20) -> str:
    filled = int(round(min(ratio, 1.0) * width))
    return "▰" * filled + "▱" * (width - filled)


def _format_message(mrr: float, mrr_source: str, anthropic_30d: float, total_30d: float, events: int) -> str:
    monthly_burn = anthropic_30d if anthropic_30d > 0 else 0.01
    ratio_anthropic = mrr / monthly_burn if monthly_burn > 0 else 0.0
    ratio_break_even = mrr / BREAK_EVEN_MRR
    bar = _progress_bar(ratio_anthropic)
    pct = round(ratio_anthropic * 100, 1)

    next_milestone_mrr = next((t for t in (50, 250, 500, 1000, 2000, BREAK_EVEN_MRR) if t > mrr), BREAK_EVEN_MRR)
    gap = max(0, next_milestone_mrr - mrr)
    customers_to_next = int((gap // 9) + 1) if gap > 0 else 0  # at $9/mo Pro — adjust later

    lines = [
        "🪙 *Self-funding telemetry*",
        f"`{bar}` {pct}% of Anthropic burn covered by MRR",
        "",
        f"• Real MRR: *${mrr:.2f}/mo*  _(source: {mrr_source})_",
        f"• Anthropic 30d burn: *${anthropic_30d:.2f}*",
        f"• All-providers 30d burn: *${total_30d:.2f}*  _({events:,} events)_",
        f"• Break-even target: *${BREAK_EVEN_MRR:,.0f}/mo* (you're at {ratio_break_even*100:.2f}%)",
        "",
        f"Next milestone: *${next_milestone_mrr:,.0f}/mo* — needs ~+{customers_to_next} Pro customers (or 1 Managed @ $499)",
    ]
    return "\n".join(lines)


def _post_telegram(text: str, topic: str) -> dict:
    script = ROOT / "scripts" / "tg-topic.sh"
    if not script.is_file():
        return {"posted": False, "reason": f"missing {script}"}
    proc = subprocess.run(["bash", str(script), topic, text], capture_output=True, text=True, timeout=20, check=False)
    return {"posted": proc.returncode == 0, "stdout": proc.stdout[:400], "stderr": proc.stderr[:400], "exit": proc.returncode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--topic", default=os.getenv("RICK_SELF_FUNDING_TOPIC", "ops-alerts"))
    ap.add_argument("--no-weekend", action="store_true", help="skip post on Sat/Sun")
    args = ap.parse_args()

    if args.no_weekend and datetime.now().weekday() >= 5:
        print(json.dumps({"status": "skip-weekend"}))
        return 0

    mrr, mrr_source = _real_mrr()
    anthropic_30d, total_30d, events = _trailing_30d_burn()
    text = _format_message(mrr, mrr_source, anthropic_30d, total_30d, events)

    live = os.getenv("RICK_SELF_FUNDING_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run
    if not live:
        print(text)
        print()
        print(json.dumps({"status": "dry-run", "live": False, "topic": args.topic}))
        return 0

    result = _post_telegram(text, args.topic)
    print(json.dumps({"status": "ok" if result["posted"] else "post-failed", "topic": args.topic, "result": result}))
    return 0 if result["posted"] else 1


if __name__ == "__main__":
    sys.exit(main())
