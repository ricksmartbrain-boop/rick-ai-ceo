#!/usr/bin/env python3
"""Sentry issue poller — fires `sentry_error_ingested` events into Rick's
event bus for Archer (engineering bug-triage persona) to act on.

Polls Sentry's REST API every 15 min, dedupes via state file, dispatches
new issues only. Same pattern as stripe-poll.py.

Auth: requires SENTRY_AUTH_TOKEN env var (separate from SENTRY_DSN — the
DSN is write-only for SDK error capture; the API token is read-only for
querying issues). Generate at:
  https://sentry.io/settings/account/api/auth-tokens/
  Required scope: project:read

If SENTRY_AUTH_TOKEN is unset, this script exits cleanly with a status
message — Archer simply won't see real errors until the token lands. The
wiring is in place ready for the day Vlad provisions the token.

Override: RICK_ARCHER_DISABLED=1 to silence dispatch.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
STATE_FILE = DATA_ROOT / "operations" / "sentry-poll-state.json"
LOG_FILE = DATA_ROOT / "operations" / "sentry-poll.jsonl"

# Cache the last N seen issue IDs so we don't re-fire across runs.
MAX_REMEMBERED_IDS = 500


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now_iso(), **payload}) + "\n")


def _read_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_run_at": None, "seen_ids": []}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_run_at": None, "seen_ids": []}


def _write_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _parse_dsn_project_id(dsn: str) -> str | None:
    """Extract project_id from SENTRY_DSN like:
        https://abc@o12345.ingest.us.sentry.io/9876543210
    Returns '9876543210' or None if format unexpected.
    """
    m = re.search(r"/(\d+)/?$", dsn or "")
    return m.group(1) if m else None


def _parse_dsn_org_id(dsn: str) -> str | None:
    """Extract org slug from SENTRY_DSN like:
        https://abc@o12345.ingest.us.sentry.io/9876543210
    Returns 'o12345' (the org ID with 'o' prefix) or None.
    """
    m = re.search(r"@(o\d+)\.", dsn or "")
    return m.group(1) if m else None


def _fetch_issues(token: str, org: str, project_id: str, limit: int = 25) -> list[dict]:
    """GET /api/0/organizations/{org}/issues/?project={project_id}&statsPeriod=24h"""
    url = (
        f"https://sentry.io/api/0/organizations/{org}/issues/"
        f"?project={project_id}&statsPeriod=24h&limit={limit}&sort=date"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _dispatch_archer(issue: dict) -> bool:
    """Fire sentry_error_ingested event into Rick's event bus."""
    try:
        from runtime.db import connect
        from runtime.engine import dispatch_event
    except ImportError as exc:
        _log({"event": "import_failed", "error": str(exc)[:200]})
        return False

    payload = {
        "issue_id": issue.get("id"),
        "short_id": issue.get("shortId"),
        "title": (issue.get("title") or "")[:300],
        "culprit": (issue.get("culprit") or "")[:300],
        "level": issue.get("level"),
        "type": issue.get("type"),
        "platform": issue.get("platform"),
        "permalink": issue.get("permalink"),
        "first_seen": issue.get("firstSeen"),
        "last_seen": issue.get("lastSeen"),
        "count": issue.get("count"),
        "user_count": issue.get("userCount"),
    }
    con = connect()
    try:
        dispatch_event(con, None, None, "sentry_error_ingested", payload)
        _log({"event": "dispatched", "issue_id": payload["issue_id"], "title": payload["title"]})
        return True
    except Exception as exc:
        _log({"event": "dispatch_failed", "issue_id": payload.get("issue_id"), "error": str(exc)[:200]})
        return False
    finally:
        con.close()


def main() -> int:
    if os.getenv("RICK_ARCHER_DISABLED", "").strip().lower() in ("1", "true", "yes"):
        print("[sentry-poll] disabled via RICK_ARCHER_DISABLED")
        return 0

    token = os.getenv("SENTRY_AUTH_TOKEN", "").strip()
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not token:
        print("[sentry-poll] SENTRY_AUTH_TOKEN not set — Archer will not see real errors.")
        print("              Generate at https://sentry.io/settings/account/api/auth-tokens/ (project:read scope)")
        _log({"event": "no_token", "reason": "SENTRY_AUTH_TOKEN unset"})
        return 0
    if not dsn:
        print("[sentry-poll] SENTRY_DSN not set — cannot derive org+project.")
        return 1

    org = _parse_dsn_org_id(dsn)
    project_id = _parse_dsn_project_id(dsn)
    if not (org and project_id):
        print(f"[sentry-poll] Could not parse org/project from SENTRY_DSN ({dsn[:40]}...)")
        return 1

    state = _read_state()
    seen_ids = set(state.get("seen_ids", []))

    try:
        issues = _fetch_issues(token, org, project_id)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        print(f"[sentry-poll] HTTP {exc.code}: {body}", file=sys.stderr)
        _log({"event": "http_error", "code": exc.code, "body": body[:200]})
        return 1
    except Exception as exc:
        print(f"[sentry-poll] fetch failed: {exc}", file=sys.stderr)
        _log({"event": "fetch_failed", "error": str(exc)[:200]})
        return 1

    new_issues = [iss for iss in issues if iss.get("id") and iss["id"] not in seen_ids]

    if not new_issues:
        print(f"[sentry-poll] no new issues (cached={len(seen_ids)}, fetched={len(issues)})")
        state["last_run_at"] = _now_iso()
        _write_state(state)
        return 0

    dispatched = 0
    for iss in new_issues:
        if _dispatch_archer(iss):
            seen_ids.add(iss["id"])
            dispatched += 1

    # Trim seen_ids to most recent N
    seen_ids_list = list(seen_ids)
    if len(seen_ids_list) > MAX_REMEMBERED_IDS:
        seen_ids_list = seen_ids_list[-MAX_REMEMBERED_IDS:]
    state["seen_ids"] = seen_ids_list
    state["last_run_at"] = _now_iso()
    _write_state(state)

    print(f"[sentry-poll] dispatched {dispatched}/{len(new_issues)} new issues to Archer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
