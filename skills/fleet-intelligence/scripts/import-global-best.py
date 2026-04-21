#!/usr/bin/env python3
"""Import global-best learnings from the Hive back into this Rick's local pool.

Calls /hive/global-best + /hive/patterns; registers each returned variant via
runtime.variants.register_variant (idempotent on prompt_hash), and INSERT OR
IGNOREs each shared pattern tagged pattern_kind='dream_insight_global'.

Endpoints not live yet (J3 pending) — 404 is handled gracefully. Exit 0 either way.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2].parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect as db_connect  # noqa: E402
from runtime.variants import register_variant  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LEDGER = DATA_ROOT / "operations" / "hive-imports.jsonl"
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(Path.home() / "clawd" / "config" / "rick.env")))

FALLBACK_SKILLS = [
    "pitch_draft", "sequence_draft", "build_draft",
    "proof_generate", "email_automation", "lead_qualify", "outreach_drafting",
    "format_multi", "publish_linkedin", "publish_newsletter",
]


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


def _api_base() -> str:
    b = os.getenv("MEETRICK_API_URL", "https://api.meetrick.ai/api/v1").rstrip("/")
    return b if b.endswith("/api/v1") else b + "/api/v1"


def _get(path: str, params: dict | None = None) -> tuple[int, dict | str]:
    url = _api_base() + path
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "rick-fleet-intel/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            body = r.read().decode("utf-8", "ignore")
            try:
                return r.status, json.loads(body)
            except json.JSONDecodeError:
                return r.status, body[:300]
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if e.fp else b"").decode("utf-8", "ignore")[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _log(event: dict):
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": datetime.now().isoformat(timespec="seconds"), **event}) + "\n")
    except OSError:
        pass


def list_local_skills(conn) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT DISTINCT skill_name FROM skill_variants ORDER BY skill_name"
        ).fetchall()
        local = [r["skill_name"] for r in rows if r["skill_name"]]
        return local or FALLBACK_SKILLS
    except Exception:
        return FALLBACK_SKILLS


def import_variants(conn, dry: bool, skills: list[str]) -> dict:
    status, body = _get("/hive/global-best", {"skills": ",".join(skills[:20])})
    if status == 404 or status == 0:
        _log({"action": "skip-variants", "status": status, "reason": "endpoint unavailable"})
        return {"status": status, "imported": 0}
    if not isinstance(body, dict):
        return {"status": status, "imported": 0, "note": "non-dict response"}
    skills_map = body.get("skills") or body or {}
    imported = 0
    for skill_name, variants in skills_map.items():
        if not isinstance(variants, list):
            continue
        for v in variants[:3]:
            if not isinstance(v, dict):
                continue
            prompt_text = v.get("prompt_text") or ""
            if len(prompt_text) < 50:
                continue
            prompt_hash = v.get("prompt_hash") or hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]
            variant_id = f"global_{prompt_hash[:8]}"
            if dry:
                _log({"action": "dry-run-import-variant", "skill": skill_name, "variant_id": variant_id})
                imported += 1
                continue
            try:
                register_variant(conn, skill_name, prompt_text, variant_id=variant_id)
                imported += 1
                _log({"action": "imported-variant", "skill": skill_name, "variant_id": variant_id})
            except Exception as e:
                _log({"action": "failed-variant", "skill": skill_name, "error": str(e)[:200]})
    return {"status": status, "imported": imported}


def import_patterns(conn, dry: bool) -> dict:
    status, body = _get("/hive/patterns", {"kind": "dream_insight", "limit": 10})
    if status == 404 or status == 0:
        _log({"action": "skip-patterns", "status": status, "reason": "endpoint unavailable"})
        return {"status": status, "imported": 0}
    if not isinstance(body, dict):
        return {"status": status, "imported": 0}
    patterns = body.get("patterns") or []
    imported = 0
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        snippet = pat.get("snippet") or ""
        if len(snippet) < 30:
            continue
        snippet_hash = hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]
        pid = f"ep_global_{snippet_hash}"
        if dry:
            _log({"action": "dry-run-import-pattern", "id": pid})
            imported += 1
            continue
        try:
            evidence_json = json.dumps(pat.get("evidence_json") or {})
            conn.execute(
                """
                INSERT OR IGNORE INTO effective_patterns
                  (id, pattern_kind, snippet, evidence_json, applicable_skills,
                   sum_wins, sum_runs, created_at, last_used_at)
                VALUES (?, 'dream_insight_global', ?, ?, '[]', ?, 0, ?, ?)
                """,
                (pid, snippet[:4000], evidence_json,
                 int(pat.get("sum_wins") or 0),
                 datetime.now().isoformat(timespec="seconds"),
                 datetime.now().isoformat(timespec="seconds")),
            )
            imported += 1
            _log({"action": "imported-pattern", "id": pid})
        except Exception as e:
            _log({"action": "failed-pattern", "id": pid, "error": str(e)[:200]})
    conn.commit()
    return {"status": status, "imported": imported}


def main():
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--live", dest="dry_run", action="store_false")
    args = ap.parse_args()

    if not args.dry_run and os.getenv("RICK_FLEET_INTEL_LIVE") != "1":
        args.dry_run = True

    conn = db_connect()
    try:
        skills = list_local_skills(conn)
        v = import_variants(conn, args.dry_run, skills)
        p = import_patterns(conn, args.dry_run)
    finally:
        conn.close()
    print(json.dumps({"dry_run": args.dry_run, "variants": v, "patterns": p}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
