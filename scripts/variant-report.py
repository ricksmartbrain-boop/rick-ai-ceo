#!/usr/bin/env python3
"""variant-report.py — deterministic 'WHAT WORKED' report for the weekly digest.

Reads ONLY real tables (outbound_jobs, skill_variants, customers) and prints
a plain-text block the Weekly Ops + Revenue Digest cron pastes verbatim.
Zero LLM calls — this is bookkeeping, and code answers bookkeeping.

Sections:
  1. 7d sends/replies by channel (all outbound_jobs traffic)
  2. 7d sends/replies by outreach variant (email touches with skill/variant)
  3. KILL CANDIDATES: channels with 50+ touches this month and 0 replies
  4. (--retire) auto-retire active variants with >=20 sends whose reply rate
     sits in the bottom quartile of their skill — never below 2 active
     variants per skill (pick_variant needs 2+ to keep sampling).

Usage:
    python3 scripts/variant-report.py            # report only
    python3 scripts/variant-report.py --retire   # report + apply retirement
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect  # noqa: E402

RETIRE_MIN_SENDS = 20
KILL_CANDIDATE_MIN_TOUCHES = 50


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def section_by_channel(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT channel,
               COUNT(*) AS touches,
               SUM(CASE WHEN status IN ('sent','done') THEN 1 ELSE 0 END) AS sent,
               SUM(CASE WHEN json_extract(result_json,'$.outcome')='replied' THEN 1 ELSE 0 END) AS replied,
               SUM(CASE WHEN json_extract(result_json,'$.outcome')='converted' THEN 1 ELSE 0 END) AS converted
          FROM outbound_jobs
         WHERE created_at >= datetime('now','-7 day')
         GROUP BY channel
         ORDER BY sent DESC
        """
    ).fetchall()
    lines = ["BY CHANNEL (7d): channel | touches | sent | replied | converted"]
    if not rows:
        lines.append("  (no outbound touches in the last 7 days)")
    for r in rows:
        lines.append(
            f"  {r['channel']} | {r['touches']} | {r['sent']} | {r['replied']} | {r['converted']}"
        )
    return lines


def section_by_variant(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(json_extract(payload_json,'$.skill'),''),'-') AS skill,
               COALESCE(NULLIF(json_extract(payload_json,'$.variant'),''), template_id) AS variant,
               COUNT(*) AS sent,
               SUM(CASE WHEN json_extract(result_json,'$.outcome')='replied' THEN 1 ELSE 0 END) AS replied
          FROM outbound_jobs
         WHERE channel = 'email'
           AND status IN ('sent','queued')
           AND created_at >= datetime('now','-7 day')
         GROUP BY 1, 2
         ORDER BY replied DESC, sent DESC
        """
    ).fetchall()
    lines = ["BY VARIANT (7d, email): skill | variant/template | sent | replied"]
    if not rows:
        lines.append("  (no email touches in the last 7 days)")
    for r in rows:
        lines.append(f"  {r['skill']} | {r['variant']} | {r['sent']} | {r['replied']}")
    return lines


def section_kill_candidates(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT channel,
               COUNT(*) AS touches,
               SUM(CASE WHEN json_extract(result_json,'$.outcome')='replied' THEN 1 ELSE 0 END) AS replied
          FROM outbound_jobs
         WHERE created_at >= date('now','start of month')
         GROUP BY channel
        HAVING touches >= ? AND replied = 0
         ORDER BY touches DESC
        """,
        (KILL_CANDIDATE_MIN_TOUCHES,),
    ).fetchall()
    lines = [f"KILL CANDIDATES ({KILL_CANDIDATE_MIN_TOUCHES}+ touches this month, 0 replies):"]
    if not rows:
        lines.append("  (none)")
    for r in rows:
        lines.append(f"  {r['channel']} — {r['touches']} touches, 0 replies. Consider killing.")
    return lines


def retire_bottom_quartile(conn, apply: bool) -> list[str]:
    """Retire active variants with >=RETIRE_MIN_SENDS sends and a reply rate
    in the bottom quartile of their skill. Reply rate comes from the
    outbound_jobs touch ledger, not the draft-time quality heuristics."""
    lines = ["VARIANT RETIREMENT (>=20 sends, bottom-quartile reply rate):"]
    skills = [
        r["skill_name"] for r in conn.execute(
            "SELECT DISTINCT skill_name FROM skill_variants WHERE status='active'"
        ).fetchall()
    ]
    any_action = False
    for skill in skills:
        actives = [
            r["variant_id"] for r in conn.execute(
                "SELECT variant_id FROM skill_variants WHERE skill_name=? AND status='active'",
                (skill,),
            ).fetchall()
        ]
        stats = []
        for vid in actives:
            row = conn.execute(
                """
                SELECT COUNT(*) AS sent,
                       SUM(CASE WHEN json_extract(result_json,'$.outcome')='replied' THEN 1 ELSE 0 END) AS replied
                  FROM outbound_jobs
                 WHERE channel = 'email'
                   AND status = 'sent'
                   AND json_extract(payload_json,'$.skill') = ?
                   AND json_extract(payload_json,'$.variant') = ?
                """,
                (skill, vid),
            ).fetchone()
            sent = int(row["sent"] or 0)
            replied = int(row["replied"] or 0)
            if sent >= RETIRE_MIN_SENDS:
                stats.append((vid, sent, replied, replied / sent))
        if len(stats) < 2:
            continue  # not enough evidence to rank anything
        stats.sort(key=lambda s: s[3])
        best_rate = stats[-1][3]
        quartile = max(1, len(stats) // 4)
        active_count = len(actives)
        for vid, sent, replied, rate in stats[:quartile]:
            if rate >= best_rate:
                continue  # all equal — nothing is a loser yet
            if active_count <= 2:
                lines.append(f"  {skill}/{vid}: bottom quartile ({replied}/{sent}) but only {active_count} active — kept")
                continue
            any_action = True
            if apply:
                conn.execute(
                    "UPDATE skill_variants SET status='retired', retired_at=? "
                    "WHERE skill_name=? AND variant_id=? AND status='active'",
                    (_now_iso(), skill, vid),
                )
                conn.commit()
                active_count -= 1
                lines.append(f"  RETIRED {skill}/{vid} — reply rate {rate:.1%} ({replied}/{sent})")
            else:
                lines.append(f"  would retire {skill}/{vid} — reply rate {rate:.1%} ({replied}/{sent}) [run with --retire]")
    if not any_action and len(lines) == 1:
        lines.append("  (no variant has 20+ ledger sends yet — nothing to judge)")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="WHAT WORKED report for the weekly digest")
    parser.add_argument("--retire", action="store_true", help="apply bottom-quartile variant retirement")
    args = parser.parse_args()

    conn = connect()
    try:
        blocks = [
            ["WHAT WORKED — touch ledger, generated " + _now_iso()],
            section_by_channel(conn),
            section_by_variant(conn),
            section_kill_candidates(conn),
            retire_bottom_quartile(conn, apply=args.retire),
        ]
        print("\n".join("\n".join(b) for b in blocks))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
