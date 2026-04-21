#!/usr/bin/env python3
"""Publish this Rick's offered skills to the Hive referral directory.

POSTs /referral/capabilities with a flat list of skill slugs this Rick
knows how to handle — so peer Ricks can discover us via /referral/discover.
Gated by RICK_HIVE_ENABLED=1. Caches last-posted hash so we don't re-POST
unchanged capability lists (which the server rate-limits at 24h anyway).

Scheduled daily 04:30 via ai.rick.hive-capabilities.plist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "config" / "subagents.json"
ENV_FILE = Path(os.getenv("RICK_ENV_FILE", str(ROOT / "config" / "rick.env")))
CACHE_FILE = Path(os.getenv("RICK_CAPS_CACHE", str(Path.home() / ".openclaw" / "capabilities-last.json")))

# Fallback slug list if config is missing — always include Rick's own baseline.
BASELINE_SKILLS = [
    "email_automation",
    "customer_memory",
    "lead_scoring",
    "outreach_drafting",
    "newsletter",
    "social_manager",
]
SKILL_RE_MAX = 50


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
    base = os.getenv("MEETRICK_API_URL", "https://api.meetrick.ai/api/v1").rstrip("/")
    if not base.endswith("/api/v1"):
        base = base + "/api/v1"
    return base


def _normalize(slug: str) -> str:
    """Server validator requires `^[a-z_]{2,50}$` — convert liberally."""
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in slug.lower())
    out = out.strip("_")
    # Replace digits with underscores since validator is strict a-z + _.
    out = "".join(c if c.isalpha() or c == "_" else "_" for c in out)
    # Collapse runs of _ and trim.
    while "__" in out:
        out = out.replace("__", "_")
    out = out.strip("_")
    return out[:SKILL_RE_MAX]


def enumerate_capabilities() -> list[str]:
    """Flat, server-validator-safe list of skill slugs this Rick offers."""
    skills: set[str] = set(BASELINE_SKILLS)
    # Pull from subagents.json capabilities lists if present.
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        for key, data in (cfg.get("subagents") or {}).items():
            for cap in data.get("capabilities") or []:
                slug = _normalize(str(cap))
                if 2 <= len(slug) <= SKILL_RE_MAX:
                    skills.add(slug)
    except Exception:
        pass
    # Cap at 30 (server validator max).
    ordered = sorted(skills)[:30]
    return ordered


def _cache_hash(skills: list[str]) -> str:
    return hashlib.sha256(",".join(skills).encode("utf-8")).hexdigest()[:16]


def _load_cache() -> dict:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(payload: dict):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def publish_capabilities(dry_run: bool = False) -> dict:
    skills = enumerate_capabilities()
    result = {"ran_at": datetime.now().isoformat(timespec="seconds"), "skills": skills, "count": len(skills)}
    cache = _load_cache()
    new_hash = _cache_hash(skills)
    if cache.get("hash") == new_hash and not dry_run:
        result["status"] = "skip-unchanged"
        return result

    rick_id = os.getenv("RICK_ID", "").strip()
    rick_secret = os.getenv("RICK_SECRET", "").strip()
    if not rick_id or not rick_secret:
        result["status"] = "skip-no-credentials"
        return result
    if dry_run:
        result["status"] = "dry-run"
        return result

    payload = {"rick_id": rick_id, "rick_secret": rick_secret, "skills": skills}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _api_base() + "/referral/capabilities",
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "rick-capabilities/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", "ignore")[:300]
            result["status"] = f"posted-{resp.status}"
            result["response"] = body
    except urllib.error.HTTPError as exc:
        result["status"] = f"http-{exc.code}"
        try:
            result["response"] = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:
            result["response"] = ""
    except Exception as exc:
        result["status"] = f"error-{type(exc).__name__}"
        result["response"] = str(exc)[:200]
    else:
        _save_cache({"hash": new_hash, "last_posted_at": result["ran_at"], "skills": skills})
    return result


def main():
    _load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=False)
    pub = sub.add_parser("publish")
    pub.add_argument("--dry-run", action="store_true")
    list_cmd = sub.add_parser("list")
    args = ap.parse_args()

    live = os.getenv("RICK_HIVE_ENABLED") == "1"

    if args.cmd == "list" or args.cmd is None:
        print(json.dumps({"skills": enumerate_capabilities()}, indent=2))
        return 0
    dry = args.dry_run or not live
    print(json.dumps(publish_capabilities(dry_run=dry), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
