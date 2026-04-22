#!/usr/bin/env python3
"""Founder Graph builder — daily stitch of HN + GitHub + IndieHackers.

Pipeline:
  1. Pull recent founder candidates from each source.
  2. UPSERT prospect_pipeline + lead_aliases per candidate.
  3. Add prospect_graph_edges for co-occurrences (e.g. all HN posters today
     get a `cooccur_hn` edge to each other — surfaces who's launching at the
     same moment).
  4. Log every action to ~/rick-vault/data/founder-graph-YYYY-MM-DD.jsonl.

Idempotent: re-running the same day skips already-inserted candidates.

Env flags:
  RICK_FOUNDER_GRAPH_LIVE=1   — actually INSERT (default: dry-run)
  RICK_FOUNDER_GRAPH_HN=30    — HN limit
  RICK_FOUNDER_GRAPH_GH=30    — GitHub limit
  RICK_FOUNDER_GRAPH_IH=30    — IH limit
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402
from runtime.integrations.founder_graph import (  # noqa: E402
    fetch_hn_show,
    fetch_github_new_founders,
    fetch_indiehackers_products,
)

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OUTPUT_DIR = DATA_ROOT / "data"

# Per-platform default lead score. HN Show is highest-intent (founder is
# actively asking for users). GitHub recent-with-followers is medium.
# IH product page is medium-low (just "saw the username on /products").
PLATFORM_BASE_SCORE = {"hn": 4.0, "github": 2.5, "ih": 2.0}


def _upsert_prospect(conn: sqlite3.Connection, candidate: dict) -> tuple[str, bool]:
    """INSERT prospect_pipeline + lead_aliases for a candidate.

    Returns (prospect_id, newly_inserted). On UNIQUE conflict (username already
    in prospect_pipeline for this platform) we look up the existing id so
    edge-building still works.
    """
    stamp = datetime.now().isoformat(timespec="seconds")
    platform = candidate["platform"]
    username = candidate["username"]
    profile_url = candidate.get("profile_url", "")
    display_name = candidate.get("display_name", username)
    score = PLATFORM_BASE_SCORE.get(platform, 1.0)
    notes = {
        "source": f"founder-graph:{platform}",
        "display_name": display_name,
        "evidence": candidate.get("evidence", {}),
    }

    prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
    cur = conn.execute(
        """INSERT OR IGNORE INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'intake', ?, ?, ?)""",
        (prospect_id, platform, username, profile_url, score, json.dumps(notes), stamp, stamp),
    )
    if cur.rowcount == 0:
        # Look up existing id (UNIQUE on username only — see prospect_pipeline schema)
        existing = conn.execute(
            "SELECT id FROM prospect_pipeline WHERE username = ? LIMIT 1",
            (username,),
        ).fetchone()
        prospect_id = existing["id"] if existing else prospect_id
        newly_inserted = False
    else:
        newly_inserted = True

    # Aliases — handle on this platform + any cross-platform identifiers we
    # collected (only available for some sources today).
    try:
        conn.execute(
            """INSERT OR IGNORE INTO lead_aliases
                   (prospect_id, alias_value, alias_type, source_channel,
                    first_seen, last_seen, confidence)
               VALUES (?, ?, ?, ?, ?, ?, 1.0)""",
            (prospect_id, username.lower(), platform, f"founder-graph:{platform}", stamp, stamp),
        )
    except sqlite3.OperationalError:
        pass

    return prospect_id, newly_inserted


def _add_edge(conn: sqlite3.Connection, src: str, dst: str, kind: str, source: str, evidence: dict) -> bool:
    if src == dst:
        return False
    # Always order so the same pair lands on a single canonical edge regardless of ordering
    a, b = (src, dst) if src < dst else (dst, src)
    stamp = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO prospect_graph_edges
           (src_prospect_id, dst_prospect_id, edge_kind, evidence_json, source,
            first_seen, last_seen, weight)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1.0)
           ON CONFLICT(src_prospect_id, dst_prospect_id, edge_kind) DO UPDATE SET
                last_seen = excluded.last_seen,
                weight = MIN(10.0, prospect_graph_edges.weight + 0.5)""",
        (a, b, kind, json.dumps(evidence)[:2000], source, stamp, stamp),
    )
    return cur.rowcount > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hn", type=int, default=int(os.getenv("RICK_FOUNDER_GRAPH_HN", "30")))
    ap.add_argument("--gh", type=int, default=int(os.getenv("RICK_FOUNDER_GRAPH_GH", "30")))
    ap.add_argument("--ih", type=int, default=int(os.getenv("RICK_FOUNDER_GRAPH_IH", "30")))
    args = ap.parse_args()
    live = os.getenv("RICK_FOUNDER_GRAPH_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run

    sources = {
        "hn": fetch_hn_show(limit=args.hn),
        "github": fetch_github_new_founders(limit=args.gh),
        "ih": fetch_indiehackers_products(limit=args.ih),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"founder-graph-{date.today().isoformat()}.jsonl"

    summary = {
        "status": "ok",
        "live": live,
        "by_source": {k: len(v) for k, v in sources.items()},
        "inserted_prospects": 0,
        "existing_prospects": 0,
        "edges_new": 0,
    }

    conn: sqlite3.Connection | None = connect() if live else None

    with log_path.open("a", encoding="utf-8") as out_f:
        # Step 1 — upsert all candidates
        all_pids_by_source: dict[str, list[str]] = {"hn": [], "github": [], "ih": []}
        for src, candidates in sources.items():
            for cand in candidates:
                if conn is not None:
                    pid, newly = _upsert_prospect(conn, cand)
                    if newly:
                        summary["inserted_prospects"] += 1
                    else:
                        summary["existing_prospects"] += 1
                    all_pids_by_source[src].append(pid)
                    out_f.write(json.dumps({"action": "upsert", "src": src, "username": cand["username"], "prospect_id": pid, "newly_inserted": newly}) + "\n")
                else:
                    out_f.write(json.dumps({"action": "would-upsert", "src": src, "username": cand["username"]}) + "\n")

        # Step 2 — co-occurrence edges (founders launching the same day)
        if conn is not None:
            for src, edge_kind in (("hn", "cooccur_hn"), ("ih", "cooccur_ih")):
                pids = all_pids_by_source[src]
                if len(pids) < 2:
                    continue
                # Cap at first 20 so the all-pairs combinatorics stays small
                for a, b in combinations(pids[:20], 2):
                    new = _add_edge(conn, a, b, edge_kind, src, {"date": date.today().isoformat()})
                    if new:
                        summary["edges_new"] += 1
            conn.commit()

    if conn is not None:
        conn.close()

    summary["log"] = str(log_path)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
