#!/usr/bin/env python3
"""21-day multi-touch outbound sequencer for qualified_lead workflows.

Public interface
----------------
    tick(connection) -> int   # dispatch due touches; returns count

Called from engine.py heartbeat() immediately after sweep_stale_running_jobs().
Wrapped in try/except at the call site — any uncaught error here is logged
and swallowed so sequencer failures never break the heartbeat loop.

Touch schedule (days from sequence_started_at, all gated on no-prior-reply):
  Day  0 — cold email #1     (opus-personalized opener, channel=email)
  Day  3 — voice call        (ElevenLabs, skipped silently if no phone)
  Day  5 — personal note     (email, different subject/tone from Day 0)
  Day  8 — proof email       (email, meetrick.ai/blog hero post link)
  Day 12 — LinkedIn DM       (channel=linkedin, skipped if no linkedin_url)
  Day 15 — email nudge       (email, last soft follow-up before breakup)
  Day 21 — breakup email     (email, last-ever touch, closes sequence)
  ANY   — reply detected     → stage='replied', sequence halts

State is persisted in context_json['seq'] so restarts are idempotent.
The sequencer does NOT re-dispatch a touch that already has an outbound_job
with status IN ('queued','done','skipped','fenix-blocked') for the same
(workflow_id, touch_kind) pair — checked via touch_log in context_json.

Formatters used (must exist in runtime/formatters/):
  - email     → runtime/formatters/email.py    (writes to mailbox outbox)
  - linkedin  → runtime/formatters/linkedin.py (CDP DM)

Voice uses ElevenLabs REST API directly (same as scripts/elevenlabs-voice-call.py
fire_call() pattern) rather than subprocess, so we can capture the result.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
LOG_FILE = DATA_ROOT / "operations" / "sequencer.jsonl"

# ElevenLabs outbound call (from MEMORY.md)
ELEVEN_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_2101km115w7wfb4b198k8khthfnb")
ELEVEN_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_PHONE_NUMBER_ID", "")  # set in rick.env
ELEVEN_API_BASE = "https://api.elevenlabs.io/v1"
CALL_ENDPOINT = f"{ELEVEN_API_BASE}/convai/twilio/outbound-call"

# Blog proof post for Day 8
PROOF_POST_URL = "https://meetrick.ai/blog"

# Sequence definition: list of {day, kind, channel, label}
# Sorted by day ascending. The sequencer dispatches the FIRST touch whose
# day threshold has been reached and that has NOT been logged yet.
SEQUENCE: list[dict[str, Any]] = [
    {"day":  0, "kind": "email-cold-1",    "channel": "email",     "label": "Cold email #1"},
    {"day":  3, "kind": "voice-day3",       "channel": "elevenlabs","label": "ElevenLabs voice call"},
    {"day":  5, "kind": "email-personal",   "channel": "email",     "label": "Personal note"},
    {"day":  8, "kind": "email-proof",      "channel": "email",     "label": "Proof email"},
    {"day": 12, "kind": "linkedin-dm",      "channel": "linkedin",  "label": "LinkedIn DM"},
    {"day": 15, "kind": "email-nudge",      "channel": "email",     "label": "Email nudge"},
    {"day": 21, "kind": "email-breakup",    "channel": "email",     "label": "Breakup email"},
]

TERMINAL_TOUCH_KINDS = {"email-breakup"}  # after this, sequence is done

# Max touches dispatched per tick — keeps heartbeat from blocking on N LLM calls.
# With opus personalization at ~25s/call, 3 touches = ~75s max per heartbeat beat.
# Remaining workflows are picked up on the next heartbeat tick.
MAX_DISPATCHES_PER_TICK = 3
QUALIFYING_STAGES = {
    "cold-email-pending",
    "sequence-active",
}
STOP_STAGES = {"replied", "done", "closed", "unsubscribed", "disqualified"}
STOP_STATUSES = {"done", "cancelled", "failed"}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(event: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**event, "ts": _now_iso()}, sort_keys=True) + "\n")


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def _parse_context(workflow: sqlite3.Row) -> dict:
    try:
        ctx = json.loads(workflow["context_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        ctx = {}
    return ctx if isinstance(ctx, dict) else {}


def _seq_state(ctx: dict) -> dict:
    """Return the 'seq' sub-dict from context_json, creating it if absent."""
    if "seq" not in ctx or not isinstance(ctx["seq"], dict):
        ctx["seq"] = {}
    return ctx["seq"]


def _touch_done(seq: dict, kind: str) -> bool:
    """Return True if this touch kind is already in the touch_log."""
    for entry in seq.get("touch_log", []):
        if entry.get("kind") == kind:
            return True
    return False


def _days_since_start(seq: dict) -> int:
    """Days elapsed since sequence_started_at (0 if not set = Day 0 ready)."""
    started = seq.get("sequence_started_at")
    if not started:
        return 0
    try:
        return (_now() - datetime.fromisoformat(started)).days
    except (ValueError, TypeError):
        return 0


def _save_context(conn: sqlite3.Connection, workflow_id: str, ctx: dict) -> None:
    conn.execute(
        "UPDATE workflows SET context_json=?, updated_at=? WHERE id=?",
        (json.dumps(ctx), _now_iso(), workflow_id),
    )


def _set_stage(conn: sqlite3.Connection, workflow_id: str, stage: str) -> None:
    conn.execute(
        "UPDATE workflows SET stage=?, updated_at=? WHERE id=?",
        (stage, _now_iso(), workflow_id),
    )


# ---------------------------------------------------------------------------
# Reply detection
# ---------------------------------------------------------------------------

def _has_replied(conn: sqlite3.Connection, workflow: sqlite3.Row, ctx: dict) -> bool:
    """Return True if we've seen an inbound message from this lead since sequence start.

    Checks:
    1. email_threads table for any last_inbound_at after sequence_started_at
    2. Inbound IMAP watcher log if available
    """
    email = (ctx.get("email") or "").strip().lower()
    if not email:
        return False

    seq = _seq_state(ctx)
    started = seq.get("sequence_started_at")
    cutoff = started if started else "1970-01-01T00:00:00"

    try:
        row = conn.execute(
            """
            SELECT last_inbound_at FROM email_threads
             WHERE prospect_id = ? AND last_inbound_at > ?
             LIMIT 1
            """,
            (email, cutoff),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass

    # Also check by searching email_threads participants for this address
    try:
        row = conn.execute(
            """
            SELECT last_inbound_at FROM email_threads
             WHERE participants_json LIKE ? AND last_inbound_at > ?
             LIMIT 1
            """,
            (f"%{email}%", cutoff),
        ).fetchone()
        if row:
            return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# LLM personalization helpers
# ---------------------------------------------------------------------------

def _personalize_email(ctx: dict, touch_kind: str) -> tuple[str, str]:
    """Return (subject, body_md) for a touch. Uses generate_text for Day 0 (opus).
    Other days use lightweight writing-route templates with context substitution.
    """
    company = ctx.get("company") or ctx.get("name") or "there"
    email = ctx.get("email") or ""
    name = ctx.get("name") or company

    # ---- Day 0: opus-personalized cold opener ----
    if touch_kind == "email-cold-1":
        try:
            from runtime.llm import generate_text
            prompt = (
                "You are Rick, an AI CEO. Write a short, sharp cold outreach email "
                "for a Tier-A B2B lead. The goal: one reply. Not a sale.\n\n"
                f"Lead: {name} | Email: {email} | Company: {company}\n\n"
                "Format:\n"
                "SUBJECT: <subject line, max 8 words>\n"
                "BODY:\n<2-3 paragraph email, plain text, no markdown, no em dashes, no filler>\n\n"
                "Rules:\n"
                "- Open with a specific observation about their business or market position\n"
                "- Mention Rick (AI CEO at meetrick.ai) and one concrete outcome\n"
                "- CTA: one question, not a pitch\n"
                "- End: Rick\n"
                "Output ONLY subject + body as specified above."
            )
            fallback = (
                f"SUBJECT: Quick question for {company}\n"
                f"BODY:\nHi {name},\n\nI've been watching what you're building at {company} — "
                "the market is moving fast and the operators who wire AI into their ops early tend "
                "to compound. I'm Rick, an AI CEO running meetrick.ai — we help founders and operators "
                "deploy AI that actually drives revenue, not just demos.\n\n"
                "One question: what's the part of your business where a faster feedback loop "
                "would change the game the most right now?\n\nRick"
            )
            result = generate_text("review", prompt, fallback)
            content = result.content.strip()
        except Exception as exc:
            _log({"event": "llm_error", "touch": touch_kind, "error": str(exc)})
            content = (
                f"SUBJECT: Quick question for {company}\n"
                f"BODY:\nHi {name},\n\nI've been watching what you're building at {company}. "
                "I'm Rick — an AI CEO at meetrick.ai. We help operators run faster with AI that "
                "actually touches revenue.\n\nWhat's the one thing you'd automate first if you had the right system?\n\nRick"
            )

        # Parse subject/body from LLM output
        subject = f"Quick question for {company}"
        body_lines = []
        in_body = False
        for line in content.splitlines():
            if line.startswith("SUBJECT:"):
                subject = line[len("SUBJECT:"):].strip()
            elif line.startswith("BODY:"):
                in_body = True
            elif in_body:
                body_lines.append(line)
        body = "\n".join(body_lines).strip() or content
        return subject, body

    # ---- Day 5: personal note ----
    if touch_kind == "email-personal":
        subject = f"Re: {company} — just checking in"
        body = (
            f"Hi {name},\n\n"
            "Circling back — wanted to make sure my last note didn't get buried.\n\n"
            "No pitch. Just one honest question: what's the revenue bottleneck at "
            f"{company} right now that you haven't had time to tackle?\n\n"
            "Takes 30 seconds to reply. I'll build something useful around the answer.\n\n"
            "Rick\n"
            "meetrick.ai"
        )
        return subject, body

    # ---- Day 8: proof email ----
    if touch_kind == "email-proof":
        subject = f"Real numbers from an AI CEO: {company}?"
        body = (
            f"Hi {name},\n\n"
            "I know you're busy, so I'll keep this short.\n\n"
            "We published the real operating numbers from running an AI CEO: "
            f"what works, what burned cash, and what's compounding. "
            f"→ {PROOF_POST_URL}\n\n"
            f"Worth 3 minutes if you're thinking about where AI actually fits in {company}'s stack.\n\n"
            "Still happy to connect if the timing works.\n\n"
            "Rick\n"
            "meetrick.ai"
        )
        return subject, body

    # ---- Day 15: nudge ----
    if touch_kind == "email-nudge":
        subject = f"Last check-in — {company}"
        body = (
            f"Hi {name},\n\n"
            "I've reached out a few times and haven't heard back — totally understand, inboxes are brutal.\n\n"
            "If the timing is off or this isn't relevant, just reply 'not now' and I'll leave you alone.\n\n"
            "If the timing is actually right and you'd like to see what an AI CEO stack "
            f"could do for {company} in 30 days — reply and we can talk specifics.\n\n"
            "Either way, no hard feelings.\n\n"
            "Rick\n"
            "meetrick.ai"
        )
        return subject, body

    # ---- Day 21: breakup ----
    if touch_kind == "email-breakup":
        subject = f"Closing the loop — {company}"
        body = (
            f"Hi {name},\n\n"
            "I'm going to close the loop on my end.\n\n"
            "I've sent a few notes over the past few weeks — clearly the timing or fit isn't right. "
            "I respect that.\n\n"
            "If anything changes — you're scaling, hitting an ops ceiling, or just curious what "
            f"an AI CEO could unlock for {company} — meetrick.ai is there when you're ready.\n\n"
            "Wishing you a strong quarter.\n\n"
            "Rick\n"
            "meetrick.ai"
        )
        return subject, body

    # Fallback
    subject = f"Following up — {company}"
    body = f"Hi {name},\n\nJust following up. Let me know if there's a better time to connect.\n\nRick\nmeetrick.ai"
    return subject, body


def _linkedin_dm_body(ctx: dict) -> str:
    company = ctx.get("company") or ctx.get("name") or "your company"
    name = ctx.get("name") or company
    return (
        f"Hey {name} — Rick here (AI CEO at meetrick.ai). "
        f"I've been following what {company} is building. "
        "Quick question: what's the one growth lever you're not moving fast enough on right now? "
        "Happy to share what we've been doing on the AI ops side if it's relevant."
    )


# ---------------------------------------------------------------------------
# ElevenLabs voice dispatch
# ---------------------------------------------------------------------------

def _fire_voice_call(lead_name: str, lead_email: str, phone: str, *, dry_run: bool = False) -> dict:
    """Fire an outbound ElevenLabs call. Returns status dict."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return {"status": "skipped", "reason": "no ELEVENLABS_API_KEY"}

    phone_id = ELEVEN_PHONE_NUMBER_ID
    if not phone_id:
        return {"status": "skipped", "reason": "no ELEVENLABS_PHONE_NUMBER_ID"}

    if not phone or not phone.strip().lstrip("+").isdigit():
        return {"status": "skipped", "reason": f"invalid phone: {phone!r}"}

    # Business-hours gate (9am–6pm ET)
    from zoneinfo import ZoneInfo
    local_hour = _now().astimezone(ZoneInfo("America/New_York")).hour
    if not (9 <= local_hour < 18):
        return {"status": "deferred", "reason": f"outside call window (hour={local_hour} ET)"}

    payload = {
        "agent_id": ELEVEN_AGENT_ID,
        "agent_phone_number_id": phone_id,
        "to_number": phone.strip(),
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "lead_name": lead_name,
                "lead_email": lead_email,
                "product_name": "meetrick.ai",
            },
        },
    }

    if dry_run:
        return {"status": "dry-run", "payload": payload}

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            CALL_ENDPOINT,
            data=data,
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"status": "called", "conversation_id": result.get("conversation_id", "")}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        return {"status": "error", "reason": f"HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)[:300]}


# ---------------------------------------------------------------------------
# Core touch dispatcher
# ---------------------------------------------------------------------------

def _dispatch_touch(
    conn: sqlite3.Connection,
    workflow: sqlite3.Row,
    ctx: dict,
    touch: dict,
) -> bool:
    """Dispatch one touch for a workflow. Updates context_json on success.
    Returns True if a touch was sent/enqueued (even if skipped-gracefully).
    """
    wf_id = workflow["id"]
    kind = touch["kind"]
    channel = touch["channel"]
    seq = _seq_state(ctx)
    touch_log = seq.setdefault("touch_log", [])

    # Idempotency: already in log?
    if _touch_done(seq, kind):
        return False

    lead_name = ctx.get("name") or ctx.get("company") or "there"
    lead_email = (ctx.get("email") or "").strip().lower()

    result_meta: dict[str, Any] = {"kind": kind, "channel": channel}

    # ------------------------------------------------------------------ email
    if channel == "email":
        if not lead_email:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no email"
        else:
            subject, body = _personalize_email(ctx, kind)
            payload = {
                "to": lead_email,
                "subject": subject,
                "body_md": body,
                "from": os.getenv("MEETRICK_FROM_EMAIL", "Rick <rick@meetrick.ai>"),
                "lane": "distribution-lane",
                "msg_id": f"seq-{wf_id[:8]}-{kind}",
            }
            try:
                from runtime.outbound_dispatcher import fan_out
                job_ids = fan_out(
                    conn,
                    lead_id=wf_id,
                    template_id=kind,
                    channels=["email"],
                    payload=payload,
                )
                result_meta["status"] = "queued" if job_ids else "deduped"
                result_meta["outbound_job_ids"] = job_ids
            except Exception as exc:
                result_meta["status"] = "error"
                result_meta["error"] = str(exc)[:300]
                _log({"event": "dispatch_error", "wf_id": wf_id, "kind": kind, "error": str(exc)})

    # --------------------------------------------------------- elevenlabs voice
    elif channel == "elevenlabs":
        phone = (ctx.get("phone") or "").strip()
        if not phone:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no phone in context"
        else:
            call_result = _fire_voice_call(lead_name, lead_email, phone)
            result_meta.update(call_result)

    # --------------------------------------------------------------- linkedin
    elif channel == "linkedin":
        linkedin_url = (ctx.get("linkedin_url") or ctx.get("dossier", {}).get("linkedin_url") or "").strip()
        if not linkedin_url:
            result_meta["status"] = "skipped"
            result_meta["reason"] = "no linkedin_url in context"
        else:
            body = _linkedin_dm_body(ctx)
            payload = {
                "kind": "dm",
                "target_url": linkedin_url,
                "body": body,
                "lane": "distribution-lane",
                "msg_id": f"seq-{wf_id[:8]}-{kind}",
            }
            try:
                from runtime.outbound_dispatcher import fan_out
                job_ids = fan_out(
                    conn,
                    lead_id=wf_id,
                    template_id=kind,
                    channels=["linkedin"],
                    payload=payload,
                )
                result_meta["status"] = "queued" if job_ids else "deduped"
                result_meta["outbound_job_ids"] = job_ids
            except Exception as exc:
                result_meta["status"] = "error"
                result_meta["error"] = str(exc)[:300]

    else:
        result_meta["status"] = "skipped"
        result_meta["reason"] = f"unknown channel: {channel}"

    # Record in touch_log regardless of outcome (idempotency + audit trail)
    result_meta["sent_at"] = _now_iso()
    touch_log.append(result_meta)
    seq["last_touch_at"] = _now_iso()

    # On Day 0, record sequence start
    if kind == "email-cold-1" and not seq.get("sequence_started_at"):
        seq["sequence_started_at"] = _now_iso()

    _save_context(conn, wf_id, ctx)
    _log({"event": "touch_dispatched", "wf_id": wf_id, **result_meta})
    return True


# ---------------------------------------------------------------------------
# Reply classifier check
# ---------------------------------------------------------------------------

def _check_and_handle_reply(
    conn: sqlite3.Connection,
    workflow: sqlite3.Row,
    ctx: dict,
) -> bool:
    """Return True and update stage if a reply is detected. Stops sequence."""
    if _has_replied(conn, workflow, ctx):
        wf_id = workflow["id"]
        _set_stage(conn, wf_id, "replied")
        conn.execute(
            "UPDATE workflows SET status='active', updated_at=? WHERE id=?",
            (_now_iso(), wf_id),
        )
        # Record event
        conn.execute(
            """
            INSERT INTO events (workflow_id, job_id, event_type, payload_json, created_at)
            VALUES (?, NULL, 'lead_replied', '{}', ?)
            """,
            (wf_id, _now_iso()),
        )
        _log({"event": "reply_detected", "wf_id": wf_id})
        return True
    return False


# ---------------------------------------------------------------------------
# Per-workflow processor
# ---------------------------------------------------------------------------

def _process_workflow(conn: sqlite3.Connection, workflow: sqlite3.Row) -> int:
    """Evaluate and dispatch due touches for one workflow. Returns 1 if dispatched, else 0."""
    wf_id = workflow["id"]
    ctx = _parse_context(workflow)
    seq = _seq_state(ctx)

    # Stop if reply detected
    if _check_and_handle_reply(conn, workflow, ctx):
        conn.commit()
        return 0  # replied, not a new dispatch

    days = _days_since_start(seq)

    # Find the FIRST touch that is due and not yet dispatched
    for touch in SEQUENCE:
        if days < touch["day"]:
            # Not due yet — stop scanning (SEQUENCE is sorted by day)
            break
        if _touch_done(seq, touch["kind"]):
            continue
        # This touch is due and not done — dispatch it
        # Ensure stage is 'sequence-active' once we start
        if workflow["stage"] == "cold-email-pending" and touch["kind"] == "email-cold-1":
            _set_stage(conn, wf_id, "sequence-active")

        dispatched = _dispatch_touch(conn, workflow, ctx, touch)
        if dispatched:
            conn.commit()
            return 1

    # Check if sequence is fully exhausted (breakup was logged)
    if _touch_done(seq, "email-breakup"):
        conn.execute(
            "UPDATE workflows SET stage='sequence-complete', updated_at=? WHERE id=?",
            (_now_iso(), wf_id),
        )
        conn.commit()

    return 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def tick(connection: sqlite3.Connection) -> int:
    """Scan all active qualified_lead workflows and dispatch any due touches.

    Returns the total count of touches dispatched this tick.
    Called from engine.py heartbeat() after sweep_stale_running_jobs().
    """
    try:
        rows = connection.execute(
            """
            SELECT * FROM workflows
             WHERE kind = 'qualified_lead'
               AND stage NOT IN ('replied','done','closed','unsubscribed',
                                  'disqualified','sequence-complete')
               AND status NOT IN ('done','cancelled','failed')
             ORDER BY created_at ASC
            """,
        ).fetchall()
    except Exception as exc:
        _log({"event": "tick_query_error", "error": str(exc)})
        return 0

    total = 0
    for wf in rows:
        if total >= MAX_DISPATCHES_PER_TICK:
            break  # leave the rest for the next heartbeat tick
        try:
            total += _process_workflow(connection, wf)
        except Exception as exc:
            _log({"event": "workflow_error", "wf_id": wf["id"], "error": str(exc)[:500]})

    if total > 0:
        _log({"event": "tick_complete", "dispatched": total, "scanned": len(rows)})

    return total


# ---------------------------------------------------------------------------
# Smoke-test CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run sequencer tick against live DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be dispatched")
    parser.add_argument("--workflow-ids", nargs="*", help="Limit to specific workflow IDs")
    args = parser.parse_args()

    from runtime.db import connect
    conn = connect()

    if args.dry_run:
        # Just show what's pending without dispatching
        rows = conn.execute(
            """
            SELECT id, kind, stage, status, context_json FROM workflows
             WHERE kind = 'qualified_lead'
               AND stage NOT IN ('replied','done','closed','unsubscribed',
                                  'disqualified','sequence-complete')
               AND status NOT IN ('done','cancelled','failed')
            """
        ).fetchall()
        for wf in rows:
            if args.workflow_ids and wf["id"] not in args.workflow_ids:
                continue
            ctx = json.loads(wf["context_json"] or "{}")
            seq = ctx.get("seq", {})
            days = _days_since_start(seq)
            done_kinds = {e["kind"] for e in seq.get("touch_log", [])}
            next_touches = [t for t in SEQUENCE if t["day"] <= days and t["kind"] not in done_kinds]
            print(
                f"{wf['id']} | stage={wf['stage']} | days={days} | "
                f"done={list(done_kinds) or 'none'} | "
                f"next={'|'.join(t['kind'] for t in next_touches[:3]) or 'none'}"
            )
    else:
        wf_filter = set(args.workflow_ids or [])
        if wf_filter:
            # Temporarily restrict: re-run tick but filtered
            all_rows = conn.execute(
                """
                SELECT * FROM workflows
                 WHERE kind = 'qualified_lead'
                   AND stage NOT IN ('replied','done','closed','unsubscribed',
                                      'disqualified','sequence-complete')
                   AND status NOT IN ('done','cancelled','failed')
                   AND id IN ({})
                """.format(",".join("?" * len(wf_filter))),
                list(wf_filter),
            ).fetchall()
            total = 0
            for wf in all_rows:
                try:
                    total += _process_workflow(conn, wf)
                except Exception as exc:
                    print(f"ERROR {wf['id']}: {exc}")
            print(f"Dispatched {total} touches for {len(all_rows)} workflows")
        else:
            n = tick(conn)
            print(f"Dispatched {n} touches")

    conn.close()
