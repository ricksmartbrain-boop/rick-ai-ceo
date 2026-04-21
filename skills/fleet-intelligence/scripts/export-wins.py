#!/usr/bin/env python3
"""Export this Rick's proven winning variants + dream-insight patterns to the Hive.

Privacy-first: every prompt_text + evidence_json is regex-scrubbed for emails,
URLs, OpenAI-style keys, Rick license keys. Rows that LOOK like email drafts
(contain an email address in the raw prompt) are skipped entirely — belt + braces.

Runs daily at 04:00 via ai.rick.fleet-intelligence.plist. --live requires
RICK_FLEET_INTEL_LIVE=1; default dry-run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent  # skills/<x>/scripts → repo root
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect as db_connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEDGER = DATA_ROOT / "operations" / "hive-exports.jsonl"
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"https?://\S+")
OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{10,}")
LICENSE_RE = re.compile(r"\bR[PB]_[A-Fa-f0-9]{6,}\b")
LOOKS_LIKE_DRAFT = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _load_env():
    if not ENV_FILE.exists():
        return
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def scrub(text: str) -> str:
    if not text:
        return text
    out = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    out = URL_RE.sub("[REDACTED_URL]", out)
    out = OPENAI_KEY_RE.sub("[REDACTED_KEY]", out)
    out = LICENSE_RE.sub("[REDACTED_LICENSE]", out)
    return out


def _api_base() -> str:
    b = os.getenv("MEETRICK_API_URL", "https://api.meetrick.ai/api/v1").rstrip("/")
    return b if b.endswith("/api/v1") else b + "/api/v1"


def _post(path: str, body: dict) -> tuple[int, str]:
    rick_id = os.getenv("RICK_ID", "")
    rick_secret = os.getenv("RICK_SECRET", "")
    data = json.dumps({**body, "rick_id": rick_id}).encode("utf-8")
    req = urllib.request.Request(
        _api_base() + path,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {rick_secret}",
            "User-Agent": "rick-fleet-intel/1.0",
        },
        method="POST",
    )
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, r.read().decode("utf-8", "ignore")[:200]
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 2:
                return e.code, (e.read() if e.fp else b"").decode("utf-8", "ignore")[:200]
            time.sleep(1 * attempt)
        except Exception as e:
            if attempt == 2:
                return 0, f"{type(e).__name__}: {e}"[:200]
            time.sleep(1 * attempt)
    return 0, "unreachable"


def _ledger_seen(key: str) -> bool:
    if not LEDGER.exists():
        return False
    cutoff = time.time() - 30 * 86400
    try:
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("key") == key:
                try:
                    ts = datetime.fromisoformat(row.get("ran_at", "")).timestamp()
                    if ts >= cutoff:
                        return True
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass
    return False


def _ledger_write(entry: dict):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ran_at": datetime.now().isoformat(timespec="seconds"), **entry}) + "\n")


def export_variants(conn: sqlite3.Connection, dry: bool, limit: int) -> dict:
    rows = conn.execute(
        """
        SELECT id, skill_name, variant_id, prompt_hash, prompt_text, n_runs, wins, losses, sum_cost
          FROM skill_variants
         WHERE status='active'
           AND n_runs >= 20
           AND (CAST(wins AS FLOAT) / MAX(1, wins+losses)) >= 0.6
         ORDER BY (CAST(wins AS FLOAT) / MAX(1, wins+losses)) DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    sent = skipped = failed = 0
    for row in rows:
        raw = row["prompt_text"] or ""
        if LOOKS_LIKE_DRAFT.search(raw):
            _ledger_write({"action": "skip", "reason": "looks_like_email_draft", "key": row["prompt_hash"]})
            skipped += 1
            continue
        if len(raw) < 50 or len(raw) > 8000:
            skipped += 1
            continue
        key = f"v:{row['prompt_hash']}"
        if _ledger_seen(key):
            skipped += 1
            continue
        payload = {
            "skill_name": row["skill_name"],
            "prompt_hash": row["prompt_hash"],
            "prompt_text": scrub(raw),
            "n_runs": int(row["n_runs"] or 0),
            "win_rate": round((row["wins"] or 0) / max(1, (row["wins"] or 0) + (row["losses"] or 0)), 3),
            "sum_cost_usd": round(float(row["sum_cost"] or 0.0), 4),
        }
        if dry:
            _ledger_write({"action": "dry-run-variant", "key": key, "preview": payload["prompt_text"][:80]})
            sent += 1
            continue
        status, body = _post("/hive/learnings", {"kind": "variant", **payload})
        if 200 <= status < 300:
            sent += 1
            _ledger_write({"action": "posted-variant", "key": key, "status": status})
        else:
            failed += 1
            _ledger_write({"action": "failed-variant", "key": key, "status": status, "body": body})
    return {"scanned": len(rows), "sent": sent, "skipped": skipped, "failed": failed}


def export_patterns(conn: sqlite3.Connection, dry: bool, limit: int) -> dict:
    rows = conn.execute(
        """
        SELECT id, pattern_kind, snippet, evidence_json, sum_wins, sum_runs
          FROM effective_patterns
         WHERE pattern_kind='dream_insight' AND sum_wins >= 3
         ORDER BY sum_wins DESC
         LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    sent = skipped = failed = 0
    for row in rows:
        snippet = row["snippet"] or ""
        if len(snippet) < 30:
            skipped += 1
            continue
        scrubbed = scrub(snippet)
        snippet_hash = hashlib.sha256(scrubbed.encode("utf-8")).hexdigest()[:16]
        key = f"p:{snippet_hash}"
        if _ledger_seen(key):
            skipped += 1
            continue
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except json.JSONDecodeError:
            evidence = {}
        evidence_scrubbed = {k: (scrub(v) if isinstance(v, str) else v) for k, v in evidence.items()}
        payload = {
            "pattern_kind": row["pattern_kind"],
            "snippet": scrubbed,
            "snippet_hash": snippet_hash,
            "evidence_json": json.dumps(evidence_scrubbed),
            "sum_wins": int(row["sum_wins"] or 0),
        }
        if dry:
            _ledger_write({"action": "dry-run-pattern", "key": key, "preview": scrubbed[:80]})
            sent += 1
            continue
        status, body = _post("/hive/learnings", {"kind": "pattern", **payload})
        if 200 <= status < 300:
            sent += 1
            _ledger_write({"action": "posted-pattern", "key": key, "status": status})
        else:
            failed += 1
            _ledger_write({"action": "failed-pattern", "key": key, "status": status, "body": body})
    return {"scanned": len(rows), "sent": sent, "skipped": skipped, "failed": failed}


def main():
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    # Even with --live, require the master env gate.
    if not args.dry_run and os.getenv("RICK_FLEET_INTEL_LIVE") != "1":
        args.dry_run = True

    conn = db_connect()
    try:
        v = export_variants(conn, args.dry_run, args.limit)
        p = export_patterns(conn, args.dry_run, args.limit)
    finally:
        conn.close()
    print(json.dumps({"dry_run": args.dry_run, "variants": v, "patterns": p}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
