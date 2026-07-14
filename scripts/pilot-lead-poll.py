#!/usr/bin/env python3
"""pilot-lead-poll.py — bridge cloud Postgres → local Rick engine ($499 pipeline).

Polls https://api.meetrick.ai/api/v1/pilot-leads/recent (see meetrick-api
src/routes/pilot-leads-recent.js) for any pilot_leads inserted since the
last seen id. A pilot applicant is the HOTTEST lead type Rick has — they
raised their hand for the $499/mo Managed pilot. For each new lead we:

  1. Insert a prospect_pipeline row (platform='pilot-form',
     status='pilot-applied', full payload JSON in notes, score=10.0).
  2. Dispatch event_type=lead_qualified locally so the event-reactions
     config (config/event-reactions.json: lead_qualified → queue_workflow:
     deal_close) fires the deal-closer chain. The trigger message tells
     the closer this is a $499 pilot application, to respond within the
     hour, and quotes the applicant's stated bottleneck verbatim.

State:
  ~/rick-vault/operations/pilot-lead-poll-state.json
    {"last_id": "pl_<hex>"}

Logs:
  ~/rick-vault/operations/pilot-lead-poll.jsonl

Safety:
  - DRY-RUN by default. Set RICK_PILOT_LEAD_POLL_LIVE=1 to actually
    insert prospects + dispatch events into the local engine.
  - Endpoint errors (404/503/timeout/DNS) gracefully no-op so launchd
    never marks the agent as crashed.
  - Stdlib only (no requests/httpx).
  - Auth header: X-Worker-Secret (matches roast-lead bridge convention —
    single shared secret). If ROAST_INGEST_SECRET is unset, we no-op
    with a log line.
  - Sending is NOT this script's job: deal_close drafts flow through the
    gated outbox (kill_switches.is_send_allowed) like everything else.

CLI:
  --dry-run    force dry-run even if RICK_PILOT_LEAD_POLL_LIVE=1
  --limit N    cap rows fetched per run (default 20, max 100)
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Make `runtime` importable when called from cron / launchd.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_DIR = DATA_ROOT / "operations"
STATE_FILE = OPS_DIR / "pilot-lead-poll-state.json"
LOCK_FILE = OPS_DIR / "pilot-lead-poll.lock"
LOG_FILE = OPS_DIR / "pilot-lead-poll.jsonl"
CONVERSIONS_LOG = DATA_ROOT / "logs" / "conversions.log"

# Same base as the roast bridge — do NOT inherit MEETRICK_API_BASE (see
# roast-lead-poll.py P0.1 note: mixing bases caused 655 silent 404s).
API_BASE = os.getenv("MEETRICK_ROAST_API_BASE", "https://api.meetrick.ai")
USER_AGENT = "Rick-PilotLeadPoll/1.0"
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


def _log_pilot_capture(lead_id: str, payload: dict) -> None:
    """Record a real pilot application in the conversion ledger.

    Live dispatch path only. Synthetic/test leads (@example.com) stay out
    of conversions.log so grading sees real captures, not instrumentation.
    """
    email = (payload.get("email") or "").strip()
    if not email:
        return
    if email.lower().endswith("@example.com"):
        return
    try:
        CONVERSIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": _utcnow_iso(),
            "event": "pilot_application",
            "stage": "capture",
            "lead_id": lead_id,
            "email": email,
            "company": payload.get("company") or "",
            "website": payload.get("website") or "",
            "source": "pilot-form",
        }
        with CONVERSIONS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        _log("conversion_log.error", lead_id=lead_id, error=str(e)[:200])


# ---------------------------------------------------------------------------
# HTTP fetch (stdlib only)
# ---------------------------------------------------------------------------

def _fetch_recent(since_id: str, limit: int, secret: str) -> dict | None:
    """GET /api/v1/pilot-leads/recent. Returns parsed dict or None on any
    error (404/503/timeout/DNS — caller no-ops)."""
    qs = urllib.parse.urlencode({"since_id": since_id, "limit": str(limit)})
    url = f"{API_BASE}/api/v1/pilot-leads/recent?{qs}"
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
        # 404 (route not deployed) / 401 (bad secret) / 503 (deploying)
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
# Payload build + local processing
# ---------------------------------------------------------------------------

def _build_payload(lead: dict) -> dict:
    """Shape the deal_close trigger payload. `message` is the closer's
    briefing — it must scream how hot this lead is."""
    name = (lead.get("name") or "").strip()
    email = (lead.get("email") or "").strip()
    company = (lead.get("company") or "").strip()
    website = (lead.get("website") or "").strip()
    bottleneck = (lead.get("notes") or "").strip()

    who = name or email or "someone"
    if company:
        who += f" ({company})"
    if bottleneck:
        bottleneck_line = f'Their stated bottleneck, verbatim: "{bottleneck}"'
    else:
        bottleneck_line = (
            "They left the bottleneck field blank — open with one sharp "
            "question about where their pipeline leaks."
        )
    message = (
        f"PILOT APPLICATION — hottest lead type Rick has. {who} applied "
        f"for the $499/mo Managed pilot via meetrick.ai/pilot. Respond "
        f"within the hour while it's warm. {bottleneck_line}"
    )

    return {
        "email": email,
        "name": name,
        "source": "pilot-form",
        "message": message,
        "company": company,
        "website": website,
        "pilot_lead_id": (lead.get("id") or "").strip(),
    }


def _insert_prospect(conn: sqlite3.Connection, lead: dict, payload: dict) -> str:
    """Insert prospect_pipeline row (status='pilot-applied'). Idempotent on
    (platform, username) so a retried batch never duplicates. Note: the
    table has no stage/source/metadata columns — the local schema maps
    stage→status, source→platform, metadata→notes JSON."""
    email = payload["email"] or "unknown"
    existing = conn.execute(
        "SELECT id FROM prospect_pipeline WHERE platform = 'pilot-form' AND username = ? LIMIT 1",
        (email,),
    ).fetchone()
    if existing:
        return existing[0]

    stamp = datetime.now().isoformat(timespec="seconds")
    prospect_id = f"pr_{uuid.uuid4().hex[:12]}"
    notes = {
        "source": "pilot-form",
        "pilot_lead": lead,  # full API payload, verbatim
    }
    conn.execute(
        """INSERT INTO prospect_pipeline
           (id, platform, username, profile_url, score, status, notes, created_at, updated_at)
           VALUES (?, 'pilot-form', ?, ?, 10.0, 'pilot-applied', ?, ?, ?)""",
        (prospect_id, email, payload["website"], json.dumps(notes, ensure_ascii=False, default=str), stamp, stamp),
    )
    return prospect_id


def _process_live(lead: dict, payload: dict) -> tuple[bool, str]:
    """Insert prospect + dispatch event_type=lead_qualified via
    runtime.engine.dispatch_event (→ queue_workflow: deal_close).

    Returns (ok, info). Never raises — caller logs result.
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
        prospect_id = _insert_prospect(conn, lead, payload)
        dispatch_event(conn, None, None, "lead_qualified", payload)
        try:
            conn.commit()
        except Exception:
            pass
        return True, f"dispatched prospect={prospect_id}"
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
    parser = argparse.ArgumentParser(description="Pilot-lead poll — $499 pipeline intake bridge")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run regardless of env flag")
    parser.add_argument("--limit", type=int, default=20, help="Max rows per poll (default 20, max 100)")
    args = parser.parse_args()

    _ensure_dirs()

    # Single-instance lock — the cursor is read at start and written at end,
    # so two overlapping runs both see the old last_id and double-dispatch
    # (sibling roast-lead-poll hit this 2026-07-13; same pattern here).
    # Held for the whole run; released automatically on process exit.
    lock_handle = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _log("run.skip.locked")
        print("pilot-lead-poll: another instance is running — skipping (no-op).")
        return 0

    env_live = os.getenv("RICK_PILOT_LEAD_POLL_LIVE", "0").strip() == "1"
    live = env_live and not args.dry_run
    limit = max(1, min(100, args.limit))

    secret = os.getenv("ROAST_INGEST_SECRET", "").strip()
    if not secret:
        _log("run.skip.no_secret", live=live)
        print("pilot-lead-poll: ROAST_INGEST_SECRET unset — skipping (no-op).", file=sys.stderr)
        return 0

    last_id = _read_state()
    _log("run.start", live=live, dry_run=args.dry_run, env_live=env_live, last_id=last_id, limit=limit)

    resp = _fetch_recent(since_id=last_id, limit=limit, secret=secret)
    if resp is None:
        # 404/503/timeout — transient or not yet deployed. Don't advance
        # state. Don't crash launchd.
        _log("run.no_response")
        print("pilot-lead-poll: 0 leads (endpoint unreachable — logged, will retry)")
        return 0

    if not resp.get("ok"):
        _log("run.api_not_ok", status=resp.get("_status"), error=resp.get("error"))
        print(f"pilot-lead-poll: api ok=false ({resp.get('error')})", file=sys.stderr)
        return 0

    leads = resp.get("leads") or []
    api_max_id = resp.get("max_id") or last_id

    if not leads:
        _log("run.no_leads", api_max_id=api_max_id)
        print(f"pilot-lead-poll: 0 new leads (last_id={last_id or 'empty'})")
        return 0

    dispatched = 0
    skipped = 0
    failed = 0
    new_max = last_id

    for lead in leads:
        try:
            lead_id = (lead.get("id") or "").strip()
            payload = _build_payload(lead)

            # No email = nothing to close against. Should be impossible
            # (the form validates email) — log loudly if it happens.
            if not payload["email"]:
                skipped += 1
                _log("dispatch.skip_no_email", lead_id=lead_id)
                if lead_id and lead_id > new_max:
                    new_max = lead_id
                continue

            if not live:
                dispatched += 1
                _log(
                    "dispatch.dry_run",
                    lead_id=lead_id,
                    email=payload["email"][:80],
                    company=payload["company"][:80],
                )
            else:
                ok, info = _process_live(lead, payload)
                if ok:
                    dispatched += 1
                    _log(
                        "dispatch.live",
                        lead_id=lead_id,
                        email=payload["email"][:80],
                        company=payload["company"][:80],
                        info=info,
                    )
                    _log_pilot_capture(lead_id, payload)
                else:
                    failed += 1
                    _log("dispatch.fail", lead_id=lead_id, info=info)
                    # Don't advance new_max past a failed lead — retry next run.
                    continue

            if lead_id and lead_id > new_max:
                new_max = lead_id
        except Exception as e:
            failed += 1
            _log("dispatch.error", lead_id=lead.get("id"), error=str(e)[:200])

    # Advance state ONLY when live — dry-run must NOT mutate state (see
    # roast-lead-poll.py 2026-04-23 incident: dry-run consumed 2 real leads).
    if live and new_max and new_max != last_id:
        _write_state(new_max)
        _log("state.advance", from_id=last_id, to_id=new_max)
    elif (not live) and new_max and new_max != last_id:
        _log("state.advance.skipped_dry_run", would_advance_to=new_max, current=last_id)

    if live:
        summary = (
            f"pilot-lead-poll: dispatched={dispatched} skipped={skipped} "
            f"failed={failed} (live=True)"
        )
    else:
        summary = (
            f"pilot-lead-poll: would poll, would dispatch {dispatched} events "
            f"(skipped={skipped}, dry-run; flip RICK_PILOT_LEAD_POLL_LIVE=1 to ship)"
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
        print(f"pilot-lead-poll: fatal {e}", file=sys.stderr)
        # Exit 0 so launchd doesn't mark the agent as crashed.
        raise SystemExit(0)
