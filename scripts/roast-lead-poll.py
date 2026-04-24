#!/usr/bin/env python3
"""roast-lead-poll.py — bridge cloud Postgres → local Rick engine (TIER-A #4).

Polls https://api.meetrick.ai/api/v1/roast-leads/recent (added in the
sibling meetrick-api commit) for any roast_leads inserted since the last
seen id, then dispatches each as event_type=roast_request locally so the
event-reactions config (config/event-reactions.json: roast_request →
queue_workflow: deal_close) fires the deal-closer chain.

State:
  ~/rick-vault/operations/roast-lead-poll-state.json
    {"last_id": "rl_<hex>"}

Logs:
  ~/rick-vault/operations/roast-lead-poll.jsonl

Safety:
  - DRY-RUN by default. Set RICK_ROAST_LEAD_POLL_LIVE=1 to actually
    dispatch events into the local engine.
  - Endpoint may not be deployed yet; 404/503/timeout/DNS — all gracefully
    no-op so launchd never marks the agent as crashed.
  - Stdlib only (no requests/httpx).
  - Auth header: X-Worker-Secret (matches roast-lead-ingest convention).
    If ROAST_INGEST_SECRET is unset, we no-op with a log line.

CLI:
  --dry-run    force dry-run even if RICK_ROAST_LEAD_POLL_LIVE=1
  --limit N    cap rows fetched per run (default 20, max 100)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# Make `runtime` importable when called from cron / launchd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_DIR = DATA_ROOT / "operations"
STATE_FILE = OPS_DIR / "roast-lead-poll-state.json"
LOG_FILE = OPS_DIR / "roast-lead-poll.jsonl"

API_BASE = os.getenv("MEETRICK_API_BASE", "https://api.meetrick.ai")
USER_AGENT = "Rick-RoastLeadPoll/1.0"
DEFAULT_TIMEOUT = 12


# ---------------------------------------------------------------------------
# I/O helpers (defensive — never raise)
# ---------------------------------------------------------------------------

def _ensure_dirs() -> None:
    try:
        OPS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _read_state() -> str:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            v = data.get("last_id")
            if isinstance(v, str):
                return v
    except Exception:
        pass
    return ""


def _write_state(last_id: str) -> None:
    try:
        _ensure_dirs()
        STATE_FILE.write_text(
            json.dumps({"last_id": str(last_id), "updated_at": _utcnow_iso()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _log(event: str, **fields: Any) -> None:
    """Append a JSONL log line. Best-effort, never raises."""
    try:
        _ensure_dirs()
        rec = {"ts": _utcnow_iso(), "event": event, **fields}
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# HTTP fetch (stdlib only)
# ---------------------------------------------------------------------------

def _fetch_recent(since_id: str, limit: int, secret: str) -> dict | None:
    """GET /api/v1/roast-leads/recent. Returns parsed dict or None on any
    error (404/503/timeout/DNS — caller no-ops)."""
    qs = urllib.parse.urlencode({"since_id": since_id, "limit": str(limit)})
    url = f"{API_BASE}/api/v1/roast-leads/recent?{qs}"
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Worker-Secret": secret,
    }
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                _log("fetch.bad_json", status=resp.status, raw=raw[:200])
                return None
    except urllib.error.HTTPError as e:
        # 404 (route not deployed yet) / 401 (bad secret) / 503 (deploying)
        body_text = ""
        try:
            body_text = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        _log("fetch.http_error", status=e.code, body=body_text)
        return None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        _log("fetch.network_error", error=str(e)[:200])
        return None
    except Exception as e:
        _log("fetch.unexpected_error", error=str(e)[:200])
        return None


# ---------------------------------------------------------------------------
# Event dispatch (local engine)
# ---------------------------------------------------------------------------

def _pick_email(lead: dict) -> str:
    """Prefer captured email; fall back to first discovered."""
    em = (lead.get("email_captured") or "").strip()
    if em:
        return em
    for cand in (lead.get("discovered_emails") or []):
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    return ""


def _build_payload(lead: dict) -> dict:
    return {
        "email": _pick_email(lead),
        "domain": (lead.get("domain") or "").strip(),
        "url": (lead.get("url") or "").strip(),
        "source": "roast_capture",
        "message": (lead.get("roast_summary") or "").strip(),
    }


def _dispatch_local(payload: dict) -> tuple[bool, str]:
    """Dispatch event_type=roast_request via runtime.engine.dispatch_event.

    Returns (ok, info). Opens a fresh sqlite connection to rick-runtime.db
    and passes (connection, workflow_id=None, job_id=None, event_type, payload).
    Never raises — caller logs result.
    """
    try:
        # Lazy import so a missing/broken runtime never breaks dry-run.
        from runtime.db import connect as runtime_connect
        from runtime.engine import dispatch_event
    except Exception as e:
        return False, f"import_error: {str(e)[:160]}"

    conn = None
    try:
        conn = runtime_connect()
        dispatch_event(conn, None, None, "roast_request", payload)
        try:
            conn.commit()
        except Exception:
            pass
        return True, "dispatched"
    except sqlite3.Error as e:
        return False, f"db_error: {str(e)[:160]}"
    except Exception as e:
        return False, f"dispatch_error: {str(e)[:160]}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Roast-lead poll — TIER-A #4")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run regardless of env flag")
    parser.add_argument("--limit", type=int, default=20, help="Max rows per poll (default 20, max 100)")
    args = parser.parse_args()

    _ensure_dirs()

    env_live = os.getenv("RICK_ROAST_LEAD_POLL_LIVE", "0").strip() == "1"
    live = env_live and not args.dry_run
    limit = max(1, min(100, args.limit))

    secret = os.getenv("ROAST_INGEST_SECRET", "").strip()
    if not secret:
        _log("run.skip.no_secret", live=live)
        print("roast-lead-poll: ROAST_INGEST_SECRET unset — skipping (no-op).", file=sys.stderr)
        return 0

    last_id = _read_state()
    _log("run.start", live=live, dry_run=args.dry_run, env_live=env_live, last_id=last_id, limit=limit)

    resp = _fetch_recent(since_id=last_id, limit=limit, secret=secret)
    if resp is None:
        # 404/503/timeout — bridge endpoint not yet deployed, or transient.
        # Don't advance state. Don't crash launchd.
        _log("run.no_response")
        print("roast-lead-poll: 0 leads (endpoint may not be deployed yet — this is OK)")
        return 0

    if not resp.get("ok"):
        _log("run.api_not_ok", status=resp.get("_status"), error=resp.get("error"))
        print(f"roast-lead-poll: api ok=false ({resp.get('error')})", file=sys.stderr)
        return 0

    leads = resp.get("leads") or []
    api_max_id = resp.get("max_id") or last_id

    if not leads:
        _log("run.no_leads", api_max_id=api_max_id)
        print(f"roast-lead-poll: 0 new leads (last_id={last_id or 'empty'})")
        return 0

    dispatched = 0
    skipped = 0
    failed = 0
    new_max = last_id

    for lead in leads:
        try:
            lead_id = (lead.get("id") or "").strip()
            payload = _build_payload(lead)

            # Skip leads with no email AND no domain — nothing useful to act on.
            if not payload["email"] and not payload["domain"]:
                skipped += 1
                _log("dispatch.skip_empty", lead_id=lead_id)
                if lead_id and lead_id > new_max:
                    new_max = lead_id
                continue

            if not live:
                dispatched += 1
                _log(
                    "dispatch.dry_run",
                    lead_id=lead_id,
                    email=payload["email"][:80],
                    domain=payload["domain"][:80],
                    source=lead.get("source"),
                )
            else:
                ok, info = _dispatch_local(payload)
                if ok:
                    dispatched += 1
                    _log(
                        "dispatch.live",
                        lead_id=lead_id,
                        email=payload["email"][:80],
                        domain=payload["domain"][:80],
                        source=lead.get("source"),
                        info=info,
                    )
                else:
                    failed += 1
                    _log("dispatch.fail", lead_id=lead_id, info=info)
                    # Don't advance new_max past a failed lead — try again next run.
                    continue

            if lead_id and lead_id > new_max:
                new_max = lead_id
        except Exception as e:
            failed += 1
            _log("dispatch.error", lead_id=lead.get("id"), error=str(e)[:200])

    # Advance state ONLY when live — dry-run must NOT mutate state, otherwise
    # a quick `--dry-run` accidentally skips real leads when live runs next
    # (verified live 2026-04-23: dry-run consumed 2 real leads, lost them
    # to the bridge until manual state reset). dry-run should be observable
    # without side effects.
    if live and new_max and new_max != last_id:
        _write_state(new_max)
        _log("state.advance", from_id=last_id, to_id=new_max)
    elif (not live) and new_max and new_max != last_id:
        _log("state.advance.skipped_dry_run", would_advance_to=new_max, current=last_id)

    if live:
        summary = (
            f"roast-lead-poll: dispatched={dispatched} skipped={skipped} "
            f"failed={failed} (live=True)"
        )
    else:
        summary = (
            f"roast-lead-poll: would poll, would dispatch {dispatched} events "
            f"(skipped={skipped}, dry-run; flip RICK_ROAST_LEAD_POLL_LIVE=1 to ship)"
        )
    print(summary)
    _log("run.done", live=live, dispatched=dispatched, skipped=skipped, failed=failed, last_id=new_max)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        _log("run.crash", error=str(e)[:300])
        print(f"roast-lead-poll: fatal {e}", file=sys.stderr)
        # Exit 0 so launchd doesn't mark the agent as crashed.
        raise SystemExit(0)
