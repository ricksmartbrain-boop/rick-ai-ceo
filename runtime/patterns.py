#!/usr/bin/env python3
"""Effective-pattern picker + outcome recorder (Rick's self-learning layer 2).

Pattern miner (`scripts/pattern-miner.py`) writes distilled lessons-learned
into the `effective_patterns` table — short snippets, attached to the skills
they help, with sum_wins / sum_runs accumulators. Until 2026-04-24 NOTHING
in Rick read those rows back out, so the entire self-learning loop was dead.
This module is the missing read+credit layer.

Shape mirrors `runtime/variants.py` (a working analog): pick → use → record.

Usage from a skill handler:

    from runtime import patterns as _patterns

    picked = _patterns.pick_patterns(connection, skill_name="pitch_draft", top_n=3)
    prompt += _patterns.format_pattern_context(picked)
    # ... run the skill, get a quality_score ...
    _patterns.record_pattern_outcome(
        connection,
        pattern_ids=[p["id"] for p in picked],
        success=quality_score >= 0.6,
    )

Safety: every public function is wrapped in try/except at the call site
discipline (callers do `try: ... except Exception: pass`). pick_patterns
returns [] on failure — handler always degrades gracefully to "no patterns
this run". record_pattern_outcome no-ops on missing IDs.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Control-arm wiring (added 2026-04-25): on every pick_patterns call where the
# caller passes a lead_id, ~10% of leads land in the "control" arm and get
# returned [] — the handler runs the skill *without* pattern injection. The
# assignment is logged so the digest can show treatment-vs-control sample
# sizes. The lift query (close-rate delta) joins the arm log to workflow
# outcomes downstream — this module only owns the assignment + log.
ARM_LOG = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault"))) \
    / "operations" / "pattern-arm-assignments.jsonl"


def _read_arm_pct() -> int:
    """Bounded parse of RICK_PATTERN_CONTROL_ARM_PCT. Clamps to [0,100] and
    falls back to 10 on malformed input — module load must NEVER raise on a
    typo'd env var, since the entire patterns module would fail to import
    and pick_patterns would crash every skill."""
    try:
        v = int((os.getenv("RICK_PATTERN_CONTROL_ARM_PCT") or "10").strip())
    except ValueError:
        v = 10
    return max(0, min(100, v))


CONTROL_ARM_PCT = _read_arm_pct()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _arm_for_lead(lead_id: str) -> str:
    """Deterministic arm assignment. Same lead_id always lands in the same arm
    so longitudinal measurement is consistent across multiple skills.

    Hash is sha1 truncated → integer mod 100. Control bucket is the first
    `CONTROL_ARM_PCT` slots — default 10 (i.e. lead_id buckets 0..9 are
    control, 10..99 are treatment).
    """
    if not lead_id:
        return "treatment"
    h = hashlib.sha1(str(lead_id).encode("utf-8")).hexdigest()
    bucket = int(h[:8], 16) % 100
    return "control" if bucket < CONTROL_ARM_PCT else "treatment"


def _log_arm_assignment(lead_id: str, skill_name: str, arm: str, picked_count: int) -> None:
    try:
        ARM_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ARM_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": _now_iso(),
                "lead_id": str(lead_id),
                "skill_name": skill_name,
                "arm": arm,
                "picked_count": int(picked_count),
            }) + "\n")
    except OSError:
        pass


def arm_assignments_summary(window_hours: int = 336) -> dict[str, Any]:
    """Read arm log over the window, return per-arm counts grouped by skill.

    Returns: {window_hours, total, by_arm: {control: N, treatment: M},
              by_skill: {skill: {control: ..., treatment: ...}}}
    """
    out: dict[str, Any] = {
        "window_hours": window_hours,
        "total": 0,
        "by_arm": {"control": 0, "treatment": 0},
        "by_skill": {},
    }
    if not ARM_LOG.exists():
        return out
    cutoff = datetime.now() - timedelta(hours=window_hours)
    try:
        text = ARM_LOG.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            if datetime.fromisoformat(entry.get("ts", "")) < cutoff:
                continue
        except (ValueError, TypeError):
            continue
        arm = entry.get("arm", "treatment")
        skill = entry.get("skill_name", "?")
        out["total"] += 1
        # setdefault → unknown arms (future "control_a", "treatment_v2", etc.)
        # are counted, not silently dropped. Total + by_arm always reconcile.
        out["by_arm"].setdefault(arm, 0)
        out["by_arm"][arm] += 1
        skill_bucket = out["by_skill"].setdefault(skill, {})
        skill_bucket.setdefault(arm, 0)
        skill_bucket[arm] += 1
    return out


def pick_patterns(
    connection: sqlite3.Connection,
    skill_name: str,
    *,
    top_n: int = 3,
    min_runs_for_proven: int = 5,
    lead_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to `top_n` highest-win-rate patterns applicable to skill_name.

    Match heuristic: applicable_skills JSON array contains skill_name (cheap
    LIKE match — works because patterns are stored as JSON-encoded arrays of
    quoted skill names, e.g. '["pitch_draft","build_draft"]') OR pattern_kind
    equals skill_name.

    Ranking: win_rate (sum_wins / (sum_runs + 1.0)) DESC. The +1.0 prior gives
    fresh patterns (sum_runs=0) a fighting chance to surface vs. one-win
    veterans, while penalizing 0/many losers. Tiebreaker: last_used_at ASC
    NULLS FIRST so unused patterns get rotated into trial.

    Returns a list of {id, snippet, pattern_kind, sum_wins, sum_runs, win_rate}.
    Empty list if no patterns apply or table empty — caller falls back to
    no-pattern prompt.

    Control-arm: when `lead_id` is provided, ~CONTROL_ARM_PCT% of leads
    deterministically land in the "control" arm and get [] returned (no
    pattern injection that run). Assignment is logged so the digest can
    surface treatment-vs-control counts and downstream lift can be measured.
    """
    if lead_id:
        arm = _arm_for_lead(lead_id)
        if arm == "control":
            _log_arm_assignment(lead_id, skill_name, "control", 0)
            return []
    try:
        like_token = f'%"{skill_name}"%'
        # Match priority:
        #   1. applicable_skills JSON array contains skill_name (explicit skill tag)
        #   2. pattern_kind equals skill_name (kind-as-skill convention)
        #   3. pattern_kind='dream_insight' with empty applicable_skills (universal
        #      cross-skill insights produced by the dreaming cycle — apply broadly)
        rows = connection.execute(
            """
            SELECT id, pattern_kind, snippet, sum_wins, sum_runs, last_used_at
              FROM effective_patterns
             WHERE applicable_skills LIKE ?
                OR pattern_kind = ?
                OR (pattern_kind = 'dream_insight' AND (applicable_skills = '[]' OR applicable_skills IS NULL))
             ORDER BY (CAST(sum_wins AS FLOAT) / (sum_runs + 1.0)) DESC,
                      last_used_at ASC NULLS FIRST,
                      sum_runs DESC
             LIMIT ?
            """,
            (like_token, skill_name, int(top_n)),
        ).fetchall()
    except sqlite3.OperationalError:
        # Table missing (fresh DB) — graceful degrade
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        sw = int(r["sum_wins"] or 0)
        sr = int(r["sum_runs"] or 0)
        out.append({
            "id": r["id"],
            "pattern_kind": r["pattern_kind"],
            "snippet": r["snippet"],
            "sum_wins": sw,
            "sum_runs": sr,
            "win_rate": (sw / sr) if sr > 0 else 0.0,
            "proven": sr >= min_runs_for_proven,
        })
    if lead_id:
        _log_arm_assignment(lead_id, skill_name, "treatment", len(out))
    return out


def record_pattern_outcome(
    connection: sqlite3.Connection,
    pattern_ids: list[str],
    *,
    success: bool,
) -> None:
    """Bump sum_runs (+1 for every pattern used) and sum_wins (+1 if success).

    Idempotent at the row level — silently skips IDs not in the table.
    Updates last_used_at on every recorded outcome so unused patterns rotate
    in via the picker's tiebreaker.
    """
    if not pattern_ids:
        return
    now = _now_iso()
    delta_win = 1 if success else 0
    try:
        for pid in pattern_ids:
            if not pid:
                continue
            connection.execute(
                """
                UPDATE effective_patterns
                   SET sum_runs = sum_runs + 1,
                       sum_wins = sum_wins + ?,
                       last_used_at = ?
                 WHERE id = ?
                """,
                (delta_win, now, str(pid)),
            )
        connection.commit()
    except sqlite3.OperationalError:
        # Don't crash the handler on a write failure — the LLM call already happened.
        pass


def format_pattern_context(patterns: list[dict[str, Any]]) -> str:
    """Render picked patterns for prompt injection.

    Returns "" if no patterns — safe to concatenate into any prompt
    unconditionally. Patterns flagged `proven` (sum_runs >= 5) get a leading
    star to weight Rick's attention; un-proven patterns are still surfaced
    as "exploration" hints.
    """
    if not patterns:
        return ""
    lines = ["", "Effective patterns from past wins (use as guidance, not gospel):"]
    for p in patterns:
        marker = "★" if p.get("proven") else "·"
        snippet = (p.get("snippet") or "").strip()
        if not snippet:
            continue
        # Cap each snippet so a verbose pattern doesn't dominate the prompt
        snippet_short = snippet[:400] + ("…" if len(snippet) > 400 else "")
        lines.append(f"  {marker} {snippet_short}")
    if len(lines) <= 2:
        return ""
    return "\n".join(lines) + "\n"


def patterns_summary(connection: sqlite3.Connection, skill_name: str | None = None) -> dict[str, Any]:
    """Reporting helper for the activity digest / dashboard. Returns counts."""
    try:
        if skill_name:
            # Match the same filter as pick_patterns (incl universal dream_insights)
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN sum_runs > 0 THEN 1 ELSE 0 END) AS used,
                       SUM(sum_wins) AS total_wins, SUM(sum_runs) AS total_runs
                  FROM effective_patterns
                 WHERE applicable_skills LIKE ?
                    OR pattern_kind = ?
                    OR (pattern_kind = 'dream_insight' AND (applicable_skills = '[]' OR applicable_skills IS NULL))
                """,
                (f'%"{skill_name}"%', skill_name),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN sum_runs > 0 THEN 1 ELSE 0 END) AS used,
                       SUM(sum_wins) AS total_wins, SUM(sum_runs) AS total_runs
                  FROM effective_patterns
                """
            ).fetchone()
    except sqlite3.OperationalError:
        return {"total": 0, "used": 0, "total_wins": 0, "total_runs": 0, "error": "table_missing"}
    return {
        "total": int(row["total"] or 0),
        "used": int(row["used"] or 0),
        "total_wins": int(row["total_wins"] or 0),
        "total_runs": int(row["total_runs"] or 0),
    }
