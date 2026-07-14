#!/usr/bin/env python3
"""Deterministic lead pipeline report for the daily lead cron."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


OUTREACH = Path.home() / "rick-vault" / "projects" / "outreach"
VAULT = Path.home() / "rick-vault"
FILES = (
    OUTREACH / "warm-pipeline.jsonl",
    OUTREACH / "sociavault-leads.jsonl",
    OUTREACH / "roast-leads.jsonl",
    VAULT / "projects" / "x-twitter" / "warm-leads.jsonl",
)
JSON_FILES = (
    OUTREACH / "targets.json",
    VAULT / "projects" / "leads" / "pipeline.json",
)
QUALIFIED_DIR = VAULT / "projects" / "qualified-leads"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            item["_source_file"] = path.name
            rows.append(item)
    return rows


def read_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("leads") if isinstance(data.get("leads"), list) else list(data.values())
    else:
        rows = []
    out = []
    for item in rows:
        if isinstance(item, dict):
            item["_source_file"] = path.name
            out.append(item)
    return out


def score(row: dict) -> int:
    explicit = row.get("score") or row.get("icp_score") or row.get("lead_score")
    if isinstance(explicit, (int, float)):
        return int(explicit)
    text = " ".join(str(row.get(k, "")) for k in ("status", "stage", "reason", "text")).lower()
    total = 0
    for word in ("buy", "pricing", "demo", "hire", "interested", "automation", "ai employee"):
        if word in text:
            total += 2
    if row.get("email"):
        total += 1
    return total


def label(row: dict) -> str:
    return (
        row.get("name")
        or row.get("author")
        or row.get("handle")
        or row.get("email")
        or row.get("url")
        or "unknown"
    )


def dedupe_key(row: dict) -> str:
    for key in ("url", "post_url", "email", "email_address", "handle", "author", "name"):
        value = row.get(key)
        if value:
            return f"{key}:{str(value).strip().lower()}"
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"row:{hashlib.sha256(payload.encode()).hexdigest()}"


def main() -> int:
    rows = []
    for path in FILES:
        rows.extend(read_jsonl(path))
    for path in JSON_FILES:
        rows.extend(read_json(path))
    if QUALIFIED_DIR.exists():
        for path in QUALIFIED_DIR.glob("*.json"):
            rows.extend(read_json(path))
    unique = {}
    for row in rows:
        key = dedupe_key(row)
        if key not in unique or score(row) > score(unique[key]):
            unique[key] = row
    rows = list(unique.values())
    rows.sort(key=score, reverse=True)
    top = [
        {
            "lead": label(row),
            "score": score(row),
            "source": row.get("_source_file"),
            "url": row.get("url") or row.get("post_url"),
            "status": row.get("status") or row.get("stage"),
        }
        for row in rows[:5]
    ]
    print(json.dumps({"unique_total": len(rows), "top": top}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
