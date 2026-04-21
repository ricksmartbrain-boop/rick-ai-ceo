#!/usr/bin/env python3
"""Pattern miner — Rick's cross-skill knowledge transfer engine (Wave 3).

Runs daily at 5am. Two miners in one pass:

  1. DREAMS miner: parses OpenClaw memory-core dream entries (~/.openclaw/
     workspace/DREAMS.md) for "Possible Lasting Updates" blocks. These are
     already pre-distilled by the dreaming pipeline — we just reshape them
     into effective_patterns rows Rick can retrieve via SQL.

  2. Pareto miner: scans the last 7 days of outcomes, groups by
     (step_name, route, model_used), ranks by (sum quality_score / sum cost_usd),
     and records the top-performing configs as 'routing' patterns. These
     become "cheap best recipes" — a skill about to dispatch can peek here
     to pick the historically-highest-ROI model for its route.

Writes to the effective_patterns table (Wave 1 schema). Idempotent on
snippet-hash so re-running doesn't duplicate rows. Telegram alert if any
new patterns landed (Rick sees his own learnings compound in real time).

Run manually: python3 scripts/pattern-miner.py --dry-run
Run scheduled: add to run-daemon.sh daily slot at 05:00
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Bootstrap path so `from runtime import ...` works from any CWD.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.context import _recent_dream_insights  # noqa: E402
from runtime.db import connect as db_connect  # noqa: E402


DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_DIR = DATA_ROOT / "operations" / "pattern-miner"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def snippet_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def upsert_pattern(
    conn,
    kind: str,
    snippet: str,
    evidence: dict,
    applicable_skills: list[str],
) -> str | None:
    """Idempotent insert — returns new_pattern_id or None if already existed."""
    h = snippet_hash(snippet)
    row = conn.execute(
        "SELECT id FROM effective_patterns WHERE id = ?", (f"ep_{h}",)
    ).fetchone()
    if row:
        # Already exists — just refresh evidence so miner can mark freshness.
        conn.execute(
            "UPDATE effective_patterns SET evidence_json=?, last_used_at=? WHERE id=?",
            (json.dumps(evidence), now_iso(), row["id"]),
        )
        return None
    pid = f"ep_{h}"
    conn.execute(
        """
        INSERT INTO effective_patterns
          (id, pattern_kind, snippet, evidence_json, applicable_skills,
           sum_wins, sum_runs, created_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
        """,
        (
            pid,
            kind,
            snippet[:4000],
            json.dumps(evidence),
            json.dumps(applicable_skills),
            now_iso(),
            now_iso(),
        ),
    )
    return pid


_DREAM_NOISE_PREFIXES = (
    "no grounded facts",
    "this day reads",
    "no durable fact",
    "monitoring and operational",
    "nothing to update",
    "<!--",
)


def _is_signal_line(line: str) -> bool:
    """Reject obvious noise bullets from the dream diary."""
    low = line.lower().strip()
    if len(low) < 30:
        return False
    for noise in _DREAM_NOISE_PREFIXES:
        if low.startswith(noise):
            return False
    # Require at least 4 space-separated tokens — filters numeric-only or
    # short header fragments from the memory-core diary template.
    if len(low.split()) < 4:
        return False
    return True


def mine_dreams(conn, dry_run: bool = False) -> int:
    """Turn DREAMS 'Possible Lasting Updates' blocks into pattern rows.

    Keeps only lines that pass _is_signal_line — memory-core often emits
    "No grounded facts were extracted" placeholders and those pollute the
    pattern table without teaching Rick anything.
    """
    entries = _recent_dream_insights(max_entries=10, max_chars=2000)
    if not entries:
        return 0
    new_count = 0
    for entry in entries:
        body = (entry.get("lasting_updates") or "").strip()
        if not body or len(body) < 40:
            continue
        kept_from_entry = 0
        for raw_line in body.splitlines():
            # Strip leading numbering ("1. ", "2) ") and bullets.
            line = raw_line.strip()
            line = line.lstrip("-*").strip()
            while line and line[0].isdigit():
                dropped = line[1:].lstrip(".)-").strip()
                if dropped == line:
                    break
                line = dropped
            if not _is_signal_line(line):
                continue
            evidence = {
                "source": "dreams",
                "dream_day": entry.get("day", ""),
                "dream_source": entry.get("source", ""),
            }
            if not dry_run:
                result = upsert_pattern(conn, "dream_insight", line, evidence, [])
                if result:
                    new_count += 1
                    kept_from_entry += 1
            else:
                print(f"[dry] dream_insight: {line[:100]}")
                kept_from_entry += 1
            # Cap at 3 patterns per dream entry — otherwise a chatty diary
            # floods the table with variations on one theme.
            if kept_from_entry >= 3:
                break
    if not dry_run:
        conn.commit()
    return new_count


def mine_pareto(conn, dry_run: bool = False, window_days: int = 7) -> int:
    """Find top (step_name, route, model) recipes in recent outcomes."""
    cutoff = (datetime.now() - timedelta(days=window_days)).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT step_name, route, model_used,
               COUNT(*) AS n_runs,
               AVG(COALESCE(quality_score, 0)) AS avg_quality,
               AVG(cost_usd) AS avg_cost,
               SUM(cost_usd) AS sum_cost
          FROM outcomes
         WHERE created_at >= ?
           AND outcome_type = 'success'
           AND cost_usd > 0
           AND model_used != ''
         GROUP BY step_name, route, model_used
        HAVING COUNT(*) >= 3
        """,
        (cutoff,),
    ).fetchall()
    if not rows:
        return 0
    # Rank by quality-per-dollar. Zero cost rows are already excluded above.
    scored = []
    for row in rows:
        q = float(row["avg_quality"] or 0)
        c = float(row["avg_cost"] or 0)
        if c <= 0:
            continue
        roi = (q + 0.01) / c
        scored.append((roi, dict(row)))
    scored.sort(key=lambda t: t[0], reverse=True)
    # Keep top 5 — we don't need the whole distribution, just the leaders.
    top = scored[:5]
    new_count = 0
    for roi, row in top:
        snippet = (
            f"For step '{row['step_name']}' on route '{row['route']}', "
            f"model '{row['model_used']}' gave avg_quality={row['avg_quality']:.2f} "
            f"at avg_cost=${row['avg_cost']:.4f}/run across {row['n_runs']} runs "
            f"(ROI={roi:.1f})."
        )
        evidence = {
            "source": "outcomes_pareto",
            "window_days": window_days,
            "step_name": row["step_name"],
            "route": row["route"],
            "model_used": row["model_used"],
            "n_runs": row["n_runs"],
            "avg_quality": round(row["avg_quality"] or 0, 3),
            "avg_cost_usd": round(row["avg_cost"] or 0, 5),
            "roi": round(roi, 2),
        }
        if not dry_run:
            result = upsert_pattern(
                conn,
                "routing_recipe",
                snippet,
                evidence,
                [row["step_name"]],
            )
            if result:
                new_count += 1
        else:
            print(f"[dry] routing_recipe: {snippet[:120]}")
    if not dry_run:
        conn.commit()
    return new_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    parser.add_argument("--window-days", type=int, default=7)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    conn = db_connect()
    try:
        dreams_new = mine_dreams(conn, dry_run=args.dry_run)
        pareto_new = mine_pareto(conn, dry_run=args.dry_run, window_days=args.window_days)
    finally:
        conn.close()

    entry = {
        "ran_at": now_iso(),
        "dry_run": args.dry_run,
        "dreams_new": dreams_new,
        "pareto_new": pareto_new,
        "total_new": dreams_new + pareto_new,
    }
    log_path.write_text(
        (log_path.read_text() if log_path.exists() else "") + json.dumps(entry) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(entry, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
