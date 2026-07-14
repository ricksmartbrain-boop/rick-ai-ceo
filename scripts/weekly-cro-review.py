#!/usr/bin/env python3
"""
weekly-cro-review.py — the kill/double/trial loop for Rick's growth machine v2.

Reads the attribution ledger for the last 14 days, groups by asset_id (and tracks
channel), computes captures/replies/calls/closes/$ per asset, and assigns a verdict:

  DOUBLE — top quartile by outcomes this period (approx for "top 25% $-per-attempt")
  TRIAL  — has outcomes but below its metric / below the DOUBLE bar
  KILL   — no outcomes (no replies/calls/closes/$) in 14 days

Writes a dated report to ~/rick-vault/control/cro-reviews/review-YYYY-MM-DD.md and
prints a 5-line summary. Reporting only — recommends, does NOT touch crons.

Usage:
  python3 scripts/weekly-cro-review.py
"""
import os
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import attribution  # noqa: E402

VAULT = os.path.expanduser("~/rick-vault")
REVIEW_DIR = os.path.join(VAULT, "control", "cro-reviews")
WINDOW_DAYS = 14

OUTCOME_STAGES = ("reply", "call_booked", "call_done", "close")


def main():
    events = attribution.read_events(days=WINDOW_DAYS)
    today = datetime.date.today().isoformat()

    # Aggregate per asset.
    assets = {}
    for e in events:
        aid = e.get("asset_id") or "(unattributed)"
        a = assets.setdefault(aid, {
            "asset_id": aid,
            "channels": set(),
            "captures": 0, "replies": 0, "calls": 0, "closes": 0,
            "amount": 0.0, "attempts": 0,
        })
        ch = e.get("channel")
        if ch:
            a["channels"].add(ch)
        stage = e.get("stage")
        a["attempts"] += 1
        if stage == "capture":
            a["captures"] += 1
        elif stage == "reply":
            a["replies"] += 1
        elif stage in ("call_booked", "call_done"):
            a["calls"] += 1
        elif stage == "close":
            a["closes"] += 1
        try:
            a["amount"] += float(e.get("amount") or 0)
        except (TypeError, ValueError):
            pass

    def outcomes(a):
        return a["replies"] + a["calls"] + a["closes"]

    def outcome_score(a):
        # Weighted: $ dominates, then closes, calls, replies. Per-attempt to
        # approximate "$-per-attempt" ranking.
        attempts = max(a["attempts"], 1)
        raw = a["amount"] + a["closes"] * 500 + a["calls"] * 100 + a["replies"] * 25
        return raw / attempts

    asset_list = list(assets.values())

    # Determine DOUBLE bar = top quartile threshold among assets WITH outcomes.
    with_outcomes = [a for a in asset_list if outcomes(a) > 0 or a["amount"] > 0]
    threshold = None
    if with_outcomes:
        scores = sorted((outcome_score(a) for a in with_outcomes), reverse=True)
        # top quartile cut index
        cut = max(0, int(len(scores) * 0.25) - 0)
        # threshold = score at the 75th percentile (index ceil(0.25*n)-1)
        idx = max(0, (len(scores) + 3) // 4 - 1)
        threshold = scores[idx]

    def verdict(a):
        if outcomes(a) == 0 and a["amount"] == 0:
            return "KILL"
        if threshold is not None and outcome_score(a) >= threshold:
            return "DOUBLE"
        return "TRIAL"

    # Order: DOUBLE first, then TRIAL, then KILL; within, by score desc.
    order = {"DOUBLE": 0, "TRIAL": 1, "KILL": 2}
    asset_list.sort(key=lambda a: (order[verdict(a)], -outcome_score(a), a["asset_id"]))

    # Build report.
    counts = {"DOUBLE": 0, "TRIAL": 0, "KILL": 0}
    total_amount = 0.0
    lines = []
    lines.append("# CRO Review — %s" % today)
    lines.append("")
    lines.append("Window: last %d days. Ledger: attribution-ledger.jsonl. "
                 "Reporting only — recommendations, no cron changes." % WINDOW_DAYS)
    lines.append("")
    lines.append("| Asset | Channels | Cap | Rep | Call | Close | $ | Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for a in asset_list:
        v = verdict(a)
        counts[v] += 1
        total_amount += a["amount"]
        lines.append("| %s | %s | %d | %d | %d | %d | %.2f | %s |" % (
            a["asset_id"], ",".join(sorted(a["channels"])) or "-",
            a["captures"], a["replies"], a["calls"], a["closes"], a["amount"], v))
    lines.append("")
    lines.append("## Recommendations")
    lines.append("- **DOUBLE (%d):** top-quartile by outcomes — increase volume/spend." % counts["DOUBLE"])
    lines.append("- **TRIAL (%d):** producing but below the bar — keep one more cycle, iterate copy/targeting." % counts["TRIAL"])
    lines.append("- **KILL (%d):** zero outcomes in %d days — recommend retiring (manual cron review)." % (counts["KILL"], WINDOW_DAYS))

    if not asset_list:
        lines.append("")
        lines.append("_No ledger events in window — nothing to review._")

    report_path = os.path.join(REVIEW_DIR, "review-%s.md" % today)
    try:
        os.makedirs(REVIEW_DIR, exist_ok=True)
        with open(report_path, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        sys.stderr.write("weekly-cro-review write WARN: %s\n" % (str(e)[:200]))

    # 5-line summary.
    print("weekly-cro-review: %d assets over %d days, $%.2f attributed" % (
        len(asset_list), WINDOW_DAYS, total_amount))
    print("  DOUBLE: %d" % counts["DOUBLE"])
    print("  TRIAL:  %d" % counts["TRIAL"])
    print("  KILL:   %d" % counts["KILL"])
    print("  report: %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
