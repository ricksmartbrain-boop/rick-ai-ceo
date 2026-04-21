#!/usr/bin/env python3
"""Skill-variant picker + outcome recorder (Rick's Wave 3 self-learning layer).

Thompson-samples active variants of a given skill based on wins/losses,
records per-run outcomes back into skill_variants, and auto-retires losers
once they accumulate enough evidence. Cross-skill pattern transfer lives in
scripts/pattern-miner.py — this file is only the picker + recorder.

Usage from a skill handler:

    from runtime.variants import pick_variant, record_variant_outcome

    variant = pick_variant(connection, skill_name="build_linkedin_post")
    prompt = variant["prompt_text"] if variant else DEFAULT_PROMPT
    # ... run the skill ...
    record_variant_outcome(
        connection,
        skill_name="build_linkedin_post",
        variant_id=variant["variant_id"] if variant else "baseline",
        won=quality_score >= 0.7,
        quality=quality_score,
        cost_usd=run_cost,
    )

If no active variants exist for the skill, pick_variant returns None and
the skill falls back to its default prompt — safe degrade.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import uuid
from datetime import datetime
from typing import Any

AUTO_RETIRE_MIN_RUNS = 30
AUTO_RETIRE_WIN_RATE_FLOOR = 0.15


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def register_variant(
    connection: sqlite3.Connection,
    skill_name: str,
    prompt_text: str,
    *,
    variant_id: str | None = None,
    parent_variant_id: str | None = None,
) -> str:
    """Idempotently register a skill variant. Returns the variant_id.

    If a variant with this (skill_name, prompt_hash) already exists, returns
    its variant_id without changing state. Lets pattern-miner + prompt-evolution
    safely call this on every tick without duplicates.
    """
    prompt_hash = _hash_prompt(prompt_text)
    existing = connection.execute(
        """
        SELECT variant_id FROM skill_variants
         WHERE skill_name = ? AND prompt_hash = ?
         LIMIT 1
        """,
        (skill_name, prompt_hash),
    ).fetchone()
    if existing:
        return existing["variant_id"] if hasattr(existing, "keys") else existing[0]
    vid = variant_id or f"v_{uuid.uuid4().hex[:8]}"
    connection.execute(
        """
        INSERT INTO skill_variants
          (id, skill_name, variant_id, prompt_hash, prompt_text,
           status, parent_variant_id, n_runs, wins, losses,
           sum_quality, sum_cost, created_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?, 0, 0, 0, 0, 0, ?)
        """,
        (
            f"sv_{uuid.uuid4().hex[:10]}",
            skill_name,
            vid,
            prompt_hash,
            prompt_text,
            parent_variant_id,
            _now_iso(),
        ),
    )
    connection.commit()
    return vid


def _beta_sample(wins: int, losses: int) -> float:
    """Thompson-sample from Beta(wins+1, losses+1).

    random.betavariate is the stdlib equivalent (no numpy dependency). +1 on
    both sides = Jeffreys-ish prior so a fresh variant with no data gets a
    ~50% expected reward, which encourages exploration on first few rolls.
    """
    return random.betavariate(max(1, wins + 1), max(1, losses + 1))


def pick_variant(
    connection: sqlite3.Connection,
    skill_name: str,
    *,
    min_variants: int = 2,
) -> dict[str, Any] | None:
    """Thompson-sample an active variant for the skill.

    Returns None if fewer than `min_variants` active rows exist — caller
    should fall back to its default prompt. This guard avoids treating a
    single-variant skill as an A/B test.
    """
    rows = connection.execute(
        """
        SELECT variant_id, prompt_text, n_runs, wins, losses, sum_quality
          FROM skill_variants
         WHERE skill_name = ? AND status = 'active'
        """,
        (skill_name,),
    ).fetchall()
    if len(rows) < min_variants:
        return None
    best_score = -1.0
    best_row = None
    for row in rows:
        score = _beta_sample(int(row["wins"] or 0), int(row["losses"] or 0))
        if score > best_score:
            best_score = score
            best_row = row
    if best_row is None:
        return None
    return {
        "variant_id": best_row["variant_id"],
        "prompt_text": best_row["prompt_text"],
        "n_runs": int(best_row["n_runs"] or 0),
        "wins": int(best_row["wins"] or 0),
        "losses": int(best_row["losses"] or 0),
        "score": best_score,
    }


def record_variant_outcome(
    connection: sqlite3.Connection,
    skill_name: str,
    variant_id: str,
    *,
    won: bool,
    quality: float = 0.0,
    cost_usd: float = 0.0,
) -> None:
    """Record one outcome against (skill_name, variant_id). Auto-retires losers.

    Idempotent at the row level — quietly no-ops if the variant isn't
    registered (so a skill using hardcoded 'baseline' without registering
    doesn't crash the pipeline).
    """
    row = connection.execute(
        "SELECT id, n_runs, wins, losses FROM skill_variants WHERE skill_name=? AND variant_id=?",
        (skill_name, variant_id),
    ).fetchone()
    if row is None:
        return
    delta_win = 1 if won else 0
    delta_loss = 0 if won else 1
    connection.execute(
        """
        UPDATE skill_variants
           SET n_runs = n_runs + 1,
               wins = wins + ?,
               losses = losses + ?,
               sum_quality = sum_quality + ?,
               sum_cost = sum_cost + ?
         WHERE id = ?
        """,
        (delta_win, delta_loss, float(quality), float(cost_usd), row["id"]),
    )
    n_runs = int(row["n_runs"] or 0) + 1
    wins = int(row["wins"] or 0) + delta_win
    # Auto-retire if the variant has enough evidence and performs poorly.
    if n_runs >= AUTO_RETIRE_MIN_RUNS:
        win_rate = wins / n_runs
        if win_rate < AUTO_RETIRE_WIN_RATE_FLOOR:
            # Don't retire if it's the last active variant (else nothing to pick).
            active_count = connection.execute(
                "SELECT COUNT(*) AS c FROM skill_variants WHERE skill_name=? AND status='active'",
                (skill_name,),
            ).fetchone()["c"]
            if active_count > 1:
                connection.execute(
                    "UPDATE skill_variants SET status='retired', retired_at=? WHERE id=?",
                    (_now_iso(), row["id"]),
                )
    connection.commit()


def leaderboard(
    connection: sqlite3.Connection,
    skill_name: str | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Top-performing variants for reporting / the revenue dashboard."""
    if skill_name:
        rows = connection.execute(
            """
            SELECT skill_name, variant_id, n_runs, wins, losses,
                   sum_quality, sum_cost, status, created_at, retired_at
              FROM skill_variants
             WHERE skill_name = ? AND n_runs > 0
             ORDER BY (CAST(wins AS FLOAT) / n_runs) DESC, n_runs DESC
             LIMIT ?
            """,
            (skill_name, int(limit)),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT skill_name, variant_id, n_runs, wins, losses,
                   sum_quality, sum_cost, status, created_at, retired_at
              FROM skill_variants
             WHERE n_runs > 0
             ORDER BY (CAST(wins AS FLOAT) / n_runs) DESC, n_runs DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]
