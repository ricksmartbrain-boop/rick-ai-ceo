#!/usr/bin/env python3
"""WHOIS/RDAP firehose — daily ingest of fresh domain registrations.

Pipeline:
  1. Read candidate domains from one or more sources.
  2. RDAP-lookup each candidate (skip already-seen).
  3. Score the record (premium TLD + identified org + recency).
  4. INSERT new prospect_pipeline rows for any score >= threshold.
  5. Log every record to ~/rick-vault/data/whois-firehose-YYYY-MM-DD.jsonl.

Sources (pluggable, all optional, processed in order):
  A. ~/rick-vault/data/whois-input/YYYY-MM-DD.txt (one domain per line)
       — drop file path for any external feed (manual seed, scraper output,
         CertStream watcher, etc.). Easiest way to wire new sources.
  B. ICANN CZDS daily zone diffs (deferred — needs CZDS_TOKEN env, requires
     24-48h ICANN application approval).
  C. CertStream WebSocket feed (deferred — needs `websockets` pip dep).

Env flags:
  RICK_WHOIS_LIVE=1            — actually INSERT into prospect_pipeline (default: dry-run)
  RICK_WHOIS_MIN_SCORE=2.5     — score threshold for INSERT (default 2.5)
  RICK_WHOIS_MAX_PER_RUN=200   — RDAP lookup cap per run (default 200, polite to public servers)
  RICK_WHOIS_TLDS=ai,dev,io,co,so,app  — only process these TLDs (default list)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402
from runtime.integrations.rdap import lookup_domain, score_record  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
INPUT_DIR = DATA_ROOT / "data" / "whois-input"
SEEN_FILE = DATA_ROOT / "data" / "whois-seen.txt"
OUTPUT_DIR = DATA_ROOT / "data"


def _parse_tlds() -> set[str]:
    raw = os.getenv("RICK_WHOIS_TLDS", "ai,dev,io,co,so,app")
    return {t.strip().lower().lstrip(".") for t in raw.split(",") if t.strip()}


def _load_seen() -> set[str]:
    if not SEEN_FILE.is_file():
        return set()
    return {line.strip().lower() for line in SEEN_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def _append_seen(domains: list[str]) -> None:
    SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SEEN_FILE.open("a", encoding="utf-8") as f:
        for d in domains:
            f.write(d + "\n")


def _load_candidates(input_path: Path | None) -> list[str]:
    """Load candidate domains from the daily input file (and other sources later)."""
    candidates: list[str] = []
    paths_to_try: list[Path] = []

    if input_path:
        paths_to_try.append(input_path)
    else:
        today_file = INPUT_DIR / f"{date.today().isoformat()}.txt"
        paths_to_try.append(today_file)
        # Also pick up yesterday's leftovers (caught up in next-day run)
        from datetime import timedelta
        yest_file = INPUT_DIR / f"{(date.today() - timedelta(days=1)).isoformat()}.txt"
        if yest_file.exists():
            paths_to_try.append(yest_file)

    for path in paths_to_try:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            d = line.strip().lower().lstrip(".")
            if d and "." in d and not d.startswith("#"):
                candidates.append(d)

    # De-dup preserving order (first occurrence wins)
    seen_local: set[str] = set()
    out: list[str] = []
    for d in candidates:
        if d not in seen_local:
            seen_local.add(d)
            out.append(d)
    return out


def _insert_prospect(conn: sqlite3.Connection, domain: str, record: dict, score: float) -> str | None:
    """Insert prospect_pipeline + lead_aliases row. Returns prospect_id or None on conflict."""
    prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
    stamp = datetime.now().isoformat(timespec="seconds")
    notes = {
        "source": "whois-firehose",
        "domain": domain,
        "registrar": record.get("registrar", ""),
        "registrant_org": record.get("registrant_org", ""),
        "registrant_country": record.get("registrant_country", ""),
        "registration_date": (record.get("events") or {}).get("registration", ""),
        "abuse_email": record.get("abuse_email", ""),
        "name_servers": record.get("name_servers", []),
        "tld": domain.rsplit(".", 1)[-1] if "." in domain else "",
    }
    cur = conn.execute(
        """INSERT OR IGNORE INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, 'domain', ?, ?, ?, 'intake', ?, ?, ?)""",
        (prospect_id, domain, f"https://{domain}", float(score), json.dumps(notes), stamp, stamp),
    )
    if cur.rowcount == 0:
        return None  # duplicate username (already exists)

    # Aliases — domain itself + abuse contact email if present
    try:
        conn.execute(
            """INSERT OR IGNORE INTO lead_aliases
                   (prospect_id, alias_value, alias_type, source_channel,
                    first_seen, last_seen, confidence)
               VALUES (?, ?, 'domain', 'whois-firehose', ?, ?, 1.0)""",
            (prospect_id, domain, stamp, stamp),
        )
        if record.get("abuse_email"):
            conn.execute(
                """INSERT OR IGNORE INTO lead_aliases
                       (prospect_id, alias_value, alias_type, source_channel,
                        first_seen, last_seen, confidence)
                   VALUES (?, ?, 'email', 'whois-firehose', ?, ?, 0.5)""",
                (prospect_id, record["abuse_email"].lower(), stamp, stamp),
            )
    except sqlite3.OperationalError:
        # lead_aliases table absent (older DB) — non-fatal
        pass
    return prospect_id


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="path to a custom candidate-domain file (default: today's whois-input/)")
    ap.add_argument("--max", type=int, default=int(os.getenv("RICK_WHOIS_MAX_PER_RUN", "200")))
    ap.add_argument("--min-score", type=float, default=float(os.getenv("RICK_WHOIS_MIN_SCORE", "2.5")))
    ap.add_argument("--dry-run", action="store_true", help="never INSERT (overrides RICK_WHOIS_LIVE)")
    args = ap.parse_args()

    live = os.getenv("RICK_WHOIS_LIVE", "").strip().lower() in ("1", "true", "yes") and not args.dry_run
    tld_filter = _parse_tlds()

    candidates = _load_candidates(Path(args.input) if args.input else None)
    if not candidates:
        print(json.dumps({
            "status": "no-input",
            "hint": f"drop a file at {INPUT_DIR}/{date.today().isoformat()}.txt with one domain per line",
            "tlds_filter": sorted(tld_filter),
            "live": live,
        }))
        return 0

    seen = _load_seen()
    todo: list[str] = []
    for d in candidates:
        if d in seen:
            continue
        tld = d.rsplit(".", 1)[-1]
        if tld_filter and tld not in tld_filter:
            continue
        todo.append(d)
        if len(todo) >= args.max:
            break

    if not todo:
        print(json.dumps({"status": "no-new", "candidates_total": len(candidates), "tlds_filter": sorted(tld_filter), "live": live}))
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"whois-firehose-{date.today().isoformat()}.jsonl"

    conn: sqlite3.Connection | None = None
    if live:
        conn = connect()

    inserted = 0
    skipped_low_score = 0
    no_record = 0
    duplicates = 0
    processed_for_seen: list[str] = []

    with out_path.open("a", encoding="utf-8") as out_f:
        for domain in todo:
            record = lookup_domain(domain)
            processed_for_seen.append(domain)

            if not record or not record.get("domain"):
                no_record += 1
                out_f.write(json.dumps({"domain": domain, "outcome": "no-record"}) + "\n")
                continue

            score = score_record(record)
            line_payload = {"domain": domain, "score": score, "record": record, "outcome": "evaluated"}

            if score < args.min_score:
                skipped_low_score += 1
                line_payload["outcome"] = "below-threshold"
                out_f.write(json.dumps(line_payload) + "\n")
                continue

            if conn is not None:
                prospect_id = _insert_prospect(conn, domain, record, score)
                if prospect_id:
                    inserted += 1
                    line_payload["outcome"] = "inserted"
                    line_payload["prospect_id"] = prospect_id
                else:
                    duplicates += 1
                    line_payload["outcome"] = "duplicate"
            else:
                line_payload["outcome"] = "dry-run"

            out_f.write(json.dumps(line_payload) + "\n")

    if conn is not None:
        conn.commit()
        conn.close()

    _append_seen(processed_for_seen)

    print(json.dumps({
        "status": "ok",
        "live": live,
        "candidates_total": len(candidates),
        "processed": len(todo),
        "no_record": no_record,
        "below_threshold": skipped_low_score,
        "duplicates": duplicates,
        "inserted": inserted,
        "min_score": args.min_score,
        "tlds_filter": sorted(tld_filter),
        "log": str(out_path),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
