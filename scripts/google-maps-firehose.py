#!/usr/bin/env python3
"""Google Maps lead firehose — drives the JS CDP scraper across vertical batches.

Reads ~/clawd/config/google-maps-targets.json, picks the next batch in
rotation (round-robin via state file), runs `max_per_run` queries against the
chrome-cdp browser on `_default_port`, and INSERTs results into
prospect_pipeline + lead_aliases.

Defaults to DRY-RUN. Set RICK_GOOGLE_MAPS_LIVE=1 to write into the DB.

The chrome-cdp browser must be alive on the target port — if not, the script
exits with a non-zero status and a clear "needs manual seed" hint. One-time
manual seeds (login + browser warmth) are documented in the plan file.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from runtime.db import connect  # noqa: E402
from lib.cdp_client import is_port_alive, resolve_port, run_js_scraper  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_FILE = DATA_ROOT / "state" / "google-maps-rotation.json"
OUTPUT_DIR = DATA_ROOT / "data"
TARGETS_FILE = ROOT / "config" / "google-maps-targets.json"
SCRAPER_JS = ROOT / "scripts" / "google-maps-cdp-scraper.js"


def _next_batch(targets: dict) -> tuple[dict, int]:
    """Round-robin pick the next batch + advance the rotation cursor."""
    batches = targets.get("batches", [])
    if not batches:
        raise ValueError("no batches in targets file")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cursor = 0
    if STATE_FILE.is_file():
        try:
            cursor = int(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("cursor", 0))
        except (json.JSONDecodeError, OSError):
            cursor = 0
    cursor = cursor % len(batches)
    chosen = batches[cursor]
    next_cursor = (cursor + 1) % len(batches)
    STATE_FILE.write_text(json.dumps({"cursor": next_cursor, "last_run_batch": chosen["id"], "ran_at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    return chosen, cursor


def _insert_prospect(conn: sqlite3.Connection, item: dict, vertical: str, query: str) -> str | None:
    name = item.get("name") or ""
    maps_url = item.get("maps_url") or ""
    if not name or not maps_url:
        return None
    # Username = canonicalized maps_url (stable identifier per location)
    # Strip query params
    base = maps_url.split("?")[0]
    prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
    stamp = datetime.now().isoformat(timespec="seconds")
    notes = {
        "source": "google-maps",
        "vertical": vertical,
        "query": query,
        "name": name,
        "meta_line": item.get("meta_line", ""),
        "rating_label": item.get("rating_label", ""),
    }
    cur = conn.execute(
        """INSERT OR IGNORE INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, 'google-maps', ?, ?, 3.0, 'intake', ?, ?, ?)""",
        (prospect_id, base, maps_url, json.dumps(notes), stamp, stamp),
    )
    if cur.rowcount == 0:
        return None
    try:
        conn.execute(
            """INSERT OR IGNORE INTO lead_aliases
                   (prospect_id, alias_value, alias_type, source_channel,
                    first_seen, last_seen, confidence)
               VALUES (?, ?, 'maps_url', 'google-maps', ?, ?, 1.0)""",
            (prospect_id, base, stamp, stamp),
        )
    except sqlite3.OperationalError:
        pass
    return prospect_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", help="override default port (name or number)")
    ap.add_argument("--max-queries", type=int, default=None, help="cap queries this run (default from config max_per_run)")
    args = ap.parse_args()

    if not TARGETS_FILE.is_file():
        print(json.dumps({"status": "error", "reason": f"missing config: {TARGETS_FILE}"}))
        return 2
    if not SCRAPER_JS.is_file():
        print(json.dumps({"status": "error", "reason": f"missing scraper: {SCRAPER_JS}"}))
        return 2

    targets = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    port = resolve_port(args.port or targets.get("_default_port", 9222), env_var="RICK_GOOGLE_MAPS_PORT")

    if not is_port_alive(port):
        print(json.dumps({
            "status": "error",
            "reason": "cdp-port-down",
            "port": port,
            "hint": f"start chrome-cdp on port {port} (e.g. ai.meetrick.chrome-cdp), do one-time manual seed (login + accept consent), then re-run.",
        }))
        return 3

    batch, cursor = _next_batch(targets)
    cap = args.max_queries or int(targets.get("max_per_run", 5))
    queries = batch["queries"][:cap]
    live = os.getenv("RICK_GOOGLE_MAPS_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUTPUT_DIR / f"google-maps-firehose-{date.today().isoformat()}.jsonl"

    conn = connect() if live else None
    summary = {
        "status": "ok",
        "live": live,
        "port": port,
        "batch_id": batch["id"],
        "vertical": batch.get("vertical"),
        "cursor_was": cursor,
        "queries_run": 0,
        "items_seen": 0,
        "items_inserted": 0,
    }

    with log_path.open("a", encoding="utf-8") as out_f:
        for q in queries:
            scraper_args = ["--port", str(port), "--query", q, "--max", "30"]
            res = run_js_scraper(SCRAPER_JS, scraper_args, timeout_s=180)
            summary["queries_run"] += 1
            summary["items_seen"] += len(res.items)

            if not res.ok:
                out_f.write(json.dumps({"query": q, "outcome": "error", "summary": res.summary, "stderr": res.raw_stderr[:400]}) + "\n")
                # If the very first query fails (likely auth/captcha), stop the
                # batch so we don't burn through scrapes that all fail the same way.
                break

            for item in res.items:
                if conn is not None:
                    pid = _insert_prospect(conn, item, batch.get("vertical", "unknown"), q)
                    if pid:
                        summary["items_inserted"] += 1
                        out_f.write(json.dumps({"query": q, "outcome": "inserted", "prospect_id": pid, "name": item.get("name", "")}) + "\n")
                    else:
                        out_f.write(json.dumps({"query": q, "outcome": "duplicate-or-bad", "name": item.get("name", "")}) + "\n")
                else:
                    out_f.write(json.dumps({"query": q, "outcome": "dry-run", "item": item}) + "\n")

    if conn is not None:
        conn.commit()
        conn.close()

    summary["log"] = str(log_path)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
