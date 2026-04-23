#!/usr/bin/env python3
"""Hive sync — daily export wins + import peer best variants.

Two passes per run:
  1. EXPORT: query local skill_variants WHERE n_runs >= 5 AND win_rate >= 0.5,
     POST each to /api/v1/hive/learnings.
  2. IMPORT: GET /api/v1/hive/global-best for the skills Rick has variants
     for, merge top peer variants into local skill_variants tagged
     'imported_from_hive' so the Thompson picker treats them like native.

Gracefully no-ops if RICK_ID/RICK_SECRET unset (Rick Prime not yet
registered). Logs results to operations/hive-sync.jsonl.

Env:
  RICK_HIVE_SYNC_LIVE=1   — actually POST + INSERT (default: dry-run)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402
from runtime.integrations.hive_client import (  # noqa: E402
    post_learning,
    get_global_best,
)

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_PATH = DATA_ROOT / "operations" / "hive-sync.jsonl"

EXPORT_MIN_RUNS = int(os.getenv("RICK_HIVE_EXPORT_MIN_RUNS", "5"))
EXPORT_MIN_WIN_RATE = float(os.getenv("RICK_HIVE_EXPORT_MIN_WIN_RATE", "0.5"))


def _log(payload: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload["ts"] = datetime.now().isoformat(timespec="seconds")
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _local_winners(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        """SELECT skill_name, variant_id, prompt_text, n_runs, wins, losses, sum_cost
             FROM skill_variants
            WHERE status='active'
              AND n_runs >= ?
              AND CAST(wins AS REAL) / NULLIF(n_runs,0) >= ?
              AND COALESCE(prompt_text,'') != ''
            ORDER BY (CAST(wins AS REAL) / NULLIF(n_runs,0)) DESC, n_runs DESC
            LIMIT 50""",
        (EXPORT_MIN_RUNS, EXPORT_MIN_WIN_RATE),
    ).fetchall()
    out = []
    for r in rows:
        win_rate = r["wins"] / r["n_runs"] if r["n_runs"] else 0.0
        out.append({
            "skill_name": r["skill_name"],
            "variant_id": r["variant_id"],
            "prompt_text": r["prompt_text"],
            "win_rate": win_rate,
            "n_runs": r["n_runs"],
            "sum_cost_usd": r["sum_cost"] or 0.0,
        })
    return out


def _local_skill_names(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT skill_name FROM skill_variants WHERE status='active' LIMIT 25"
    ).fetchall()
    return [r["skill_name"] for r in rows]


def _import_peer_variant(con: sqlite3.Connection, skill: str, peer: dict) -> bool:
    prompt_text = peer.get("prompt_text") or ""
    if not prompt_text:
        return False
    prompt_hash = hashlib.sha1(f"{skill}:{prompt_text}".encode()).hexdigest()[:16]
    # Skip if we already have an active variant with this exact prompt_hash
    existing = con.execute(
        "SELECT id FROM skill_variants WHERE skill_name=? AND prompt_hash=? LIMIT 1",
        (skill, prompt_hash),
    ).fetchone()
    if existing:
        return False
    variant_id = f"hive_{prompt_hash[:8]}"
    new_id = f"sv_{uuid.uuid4().hex[:12]}"
    stamp = datetime.now().isoformat(timespec="seconds")
    con.execute(
        """INSERT INTO skill_variants
             (id, skill_name, variant_id, prompt_hash, prompt_text, status,
              parent_variant_id, n_runs, wins, losses, sum_quality, sum_cost, created_at)
           VALUES (?, ?, ?, ?, ?, 'active', NULL, 0, 0, 0, 0, 0, ?)""",
        (new_id, skill, variant_id, prompt_hash, prompt_text, stamp),
    )
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--import-only", action="store_true")
    args = ap.parse_args()
    live = os.getenv("RICK_HIVE_SYNC_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run

    rick_id = os.getenv("RICK_ID") or ""
    rick_secret = os.getenv("RICK_SECRET") or ""
    if not rick_id or not rick_secret:
        summary = {"status": "skip", "reason": "RICK_ID/RICK_SECRET unset"}
        print(json.dumps(summary))
        _log(summary)
        return 0

    con = connect()
    summary = {"status": "ok", "live": live, "exported": 0, "skipped_export": 0, "imported": 0, "skills_pulled": []}

    try:
        # ── EXPORT ─────────────────────────────────────────────────────────
        if not args.import_only:
            winners = _local_winners(con)
            for w in winners:
                if not live:
                    summary["skipped_export"] += 1
                    continue
                res = post_learning(
                    skill_name=w["skill_name"],
                    variant_id=w["variant_id"],
                    prompt_text=w["prompt_text"],
                    win_rate=w["win_rate"],
                    n_runs=w["n_runs"],
                    sum_cost_usd=w["sum_cost_usd"],
                )
                if res and res.get("ok"):
                    summary["exported"] += 1
                else:
                    _log({"export_fail": w["skill_name"], "variant_id": w["variant_id"], "response": res})

        # ── IMPORT ─────────────────────────────────────────────────────────
        if not args.export_only:
            skills = _local_skill_names(con)
            summary["skills_pulled"] = skills
            if skills:
                by_skill = get_global_best(skills) if live else {}
                for skill, peers in by_skill.items():
                    for peer in peers[:3]:
                        if _import_peer_variant(con, skill, peer):
                            summary["imported"] += 1

        if live:
            con.commit()
    finally:
        con.close()

    print(json.dumps(summary))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
