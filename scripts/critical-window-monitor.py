#!/usr/bin/env python3
"""critical-window-monitor.py — 5-min heightened reply-detection for 72h post-touch window.

Runs every 5 min via LaunchAgent (ai.rick.critical-window-monitor.plist, StartInterval=300).
Scoped to: stage='sequence-active' wfs where last touch was within 72h.

Detection sources (layered):
  (a) Resend events API — per-email event history (opened/clicked/delivered/replied)
  (b) Gmail IMAP — unread replies from target emails
  (c) reply-router.jsonl
  (d) mailbox/triage/inbound-*.jsonl

Escalation logic:
  - replied                    → P0 alert (Telegram + notify) + auto-draft via runtime.llm route='review' (NO auto-send)
  - opened + clicked (warm++)  → P1 warm alert + pre-stage draft for Vlad review
  - opened only                → warm-signal log entry (no alert)

De-escalation:
  - Per-wf: after 72h from last_touch_at → automatically drops back to standard reply-watcher
  - Global: if no wfs remain in critical window → script exits cleanly, LaunchAgent keeps polling

Auto-drafts route through runtime.llm route='review' (router enforces smart-models invariant).
Kill-switch: RICK_CRITICAL_WINDOW_LIVE=1

State:   ~/rick-vault/control/critical-window-state.json
Log:     ~/rick-vault/operations/critical-window-monitor.jsonl
Drafts:  ~/rick-vault/mailbox/drafts/auto/{wf_id}_cw.json
Digest:  ~/rick-vault/control/briefings/reply-watch-{date}.md (appended)

Usage:
    python3 scripts/critical-window-monitor.py              # live run (needs RICK_CRITICAL_WINDOW_LIVE=1)
    python3 scripts/critical-window-monitor.py --dry-run    # no DB writes / no alerts
    python3 scripts/critical-window-monitor.py --verbose    # extra output
    python3 scripts/critical-window-monitor.py --status     # show current window state
"""

from __future__ import annotations

import argparse
import imaplib
import email as email_module
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.llm import BudgetExceeded  # noqa: E402

DATA_ROOT  = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB_PATH    = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))
STATE_FILE = DATA_ROOT / "control" / "critical-window-state.json"
LOG_FILE   = DATA_ROOT / "operations" / "critical-window-monitor.jsonl"
DRAFT_DIR  = DATA_ROOT / "mailbox" / "drafts" / "auto"
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"

# Critical window: 72 hours post last-touch
CRITICAL_WINDOW_HOURS  = 72
# Reply scan cutoff: look back 7 days for replies
REPLY_SCAN_HOURS       = 7 * 24
# Drafts go through runtime.llm route='review'; the router owns model
# choice, fallbacks, and budget metering (smart-models invariant lives there).

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(ts: str) -> datetime:
    """Parse ISO timestamp → UTC datetime."""
    if not ts:
        return datetime.now(timezone.utc) - timedelta(days=365)
    s = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _elapsed_hours(since_ts: str) -> float:
    return (datetime.now(timezone.utc) - _parse_ts(since_ts)).total_seconds() / 3600


def _log(row: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"wfs_in_window": {}, "handled_replies": {}, "p0_fired": {}, "last_run": ""}


def _save_state(state: dict, dry_run: bool) -> None:
    if dry_run:
        return
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Identify wfs in critical window
# ---------------------------------------------------------------------------

def _load_critical_wfs(conn) -> list[dict]:
    """Return sequence-active ICP wfs whose last touch was within CRITICAL_WINDOW_HOURS.
    
    Sources last_touch_at from seq.last_touch_at (stored in context_json).
    Falls back to created_at for wfs that just entered sequence-active.
    """
    rows = conn.execute(
        """
        SELECT w.id, w.title, w.stage, w.context_json, w.created_at, w.updated_at,
               oj.id AS ob_id,
               json_extract(oj.result_json,'$.resend_id') AS resend_id,
               json_extract(oj.payload_json,'$.subject') AS sent_subject,
               json_extract(oj.payload_json,'$.body_md')  AS sent_body
        FROM   workflows w
        LEFT   JOIN outbound_jobs oj ON oj.lead_id = w.id
                                    AND oj.channel  = 'email'
        WHERE  w.stage = 'sequence-active'
          AND  w.kind  = 'qualified_lead'
        ORDER  BY w.created_at ASC
        """,
    ).fetchall()

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=CRITICAL_WINDOW_HOURS)
    seen: dict[str, dict] = {}

    for r in rows:
        wf_id = r["id"]
        ctx   = json.loads(r["context_json"]) if r["context_json"] else {}
        tp    = ctx.get("trigger_payload") or {}
        seq   = ctx.get("seq") or {}

        # Extract last touch timestamp
        touch_log  = seq.get("touch_log", [])
        last_touch = seq.get("last_touch_at", "")
        if not last_touch and touch_log:
            last_touch = touch_log[-1].get("sent_at", "")
        if not last_touch:
            last_touch = r["updated_at"] or r["created_at"] or ""

        # Only include if last touch was within 72h
        if _parse_ts(last_touch) < cutoff_dt:
            continue

        email = (ctx.get("email") or tp.get("email") or "").lower().strip()
        if not email:
            continue

        # Collect all resend_ids from touch_log
        resend_ids = []
        for touch in touch_log:
            for ob_id in touch.get("outbound_job_ids", []):
                pass  # ob_ids not resend_ids — will search by email
        if r["resend_id"]:
            resend_ids.append(r["resend_id"])

        if wf_id not in seen:
            expires_at = (_parse_ts(last_touch) + timedelta(hours=CRITICAL_WINDOW_HOURS)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            seen[wf_id] = {
                "wf_id":         wf_id,
                "title":         r["title"],
                "email":         email,
                "name":          ctx.get("name") or tp.get("name") or "",
                "company":       ctx.get("company") or tp.get("company") or tp.get("domain") or "",
                "icp_score":     ctx.get("icp_score") or tp.get("icp_score") or 0,
                "opener_subj":   ctx.get("opener_subject") or r["sent_subject"] or "",
                "opener_body":   ctx.get("opener_body") or r["sent_body"] or "",
                "last_touch_at": last_touch,
                "expires_at":    expires_at,
                "resend_ids":    resend_ids,
                "ob_id":         r["ob_id"] or "",
                "created_at":    r["created_at"] or "",
            }
        elif r["resend_id"] and r["resend_id"] not in seen[wf_id]["resend_ids"]:
            seen[wf_id]["resend_ids"].append(r["resend_id"])

    return list(seen.values())


# ---------------------------------------------------------------------------
# 2a. Resend events API (heightened)
# ---------------------------------------------------------------------------

def _resend_get(path: str, api_key: str, timeout: int = 12) -> dict:
    """Call Resend API via curl subprocess (avoids urllib 403 + user-agent issues)."""
    import subprocess
    url = f"https://api.resend.com{path}"
    result = subprocess.run(
        ["curl", "-sf", url, "-H", f"Authorization: Bearer {api_key}"],
        capture_output=True, text=True, timeout=timeout,
    )
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _check_resend_events(wfs: list[dict], verbose: bool = False) -> dict[str, dict]:
    """Query Resend API for per-email events with full event history.
    
    Returns {email: {"opened": bool, "clicked": bool, "replied": bool, "resend_id": str, "events": [...]}}
    Uses two strategies:
      1. Bulk list (GET /emails?limit=100) — matches by recipient email
      2. Individual fetch (GET /emails/{id}) — for stored resend_ids
    Uses curl subprocess to avoid urllib 403 / user-agent issues with Resend.
    """
    results: dict[str, dict] = {}
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        if verbose:
            print("  [resend] RESEND_API_KEY not set — skipping")
        return results

    email_set = {w["email"] for w in wfs}

    # Strategy 1: bulk list — find emails by recipient
    try:
        data = _resend_get("/emails?limit=100", api_key)
        for em_rec in data.get("data", []):
            rid       = em_rec.get("id", "")
            to_list   = [t.lower().strip() for t in em_rec.get("to", [])]
            last_evt  = em_rec.get("last_event", "")
            created   = em_rec.get("created_at", "")

            for addr in to_list:
                if addr not in email_set:
                    continue
                if addr not in results:
                    results[addr] = {
                        "opened": False, "clicked": False, "replied": False,
                        "resend_id": rid, "last_event": last_evt,
                        "created_at": created, "events": [],
                    }
                if last_evt in ("opened", "clicked", "replied"):
                    results[addr]["opened"]  = results[addr]["opened"] or last_evt in ("opened", "clicked", "replied")
                    results[addr]["clicked"] = results[addr]["clicked"] or last_evt in ("clicked", "replied")
                    results[addr]["replied"] = results[addr]["replied"] or last_evt == "replied"
                if verbose and last_evt in ("opened", "clicked", "replied"):
                    print(f"  [resend-bulk] {addr}: last_event={last_evt} rid={rid}")
    except Exception as exc:
        if verbose:
            print(f"  [resend-bulk] error: {exc}")

    # Strategy 2: individual fetch for stored resend_ids (deeper event check)
    for wf in wfs:
        for rid in wf.get("resend_ids", []):
            if not rid:
                continue
            addr = wf["email"]
            try:
                detail   = _resend_get(f"/emails/{rid}", api_key)
                last_evt = detail.get("last_event", "")
                if addr not in results:
                    results[addr] = {
                        "opened": False, "clicked": False, "replied": False,
                        "resend_id": rid, "last_event": last_evt,
                        "created_at": detail.get("created_at", ""), "events": [],
                    }
                if last_evt in ("opened", "clicked", "replied"):
                    results[addr]["opened"]  = True
                    results[addr]["clicked"] = results[addr]["clicked"] or last_evt in ("clicked", "replied")
                    results[addr]["replied"] = results[addr]["replied"] or last_evt == "replied"
                if verbose and last_evt in ("opened", "clicked", "replied"):
                    print(f"  [resend-detail] {addr}: last_event={last_evt} rid={rid}")
            except Exception as exc:
                if verbose:
                    print(f"  [resend-detail] error for {rid}: {exc}")

    return results


# ---------------------------------------------------------------------------
# 2b. Gmail IMAP
# ---------------------------------------------------------------------------

def _check_gmail_imap(target_emails: set[str], cutoff_iso: str, verbose: bool = False) -> dict[str, dict]:
    """Unread replies from target emails via Gmail IMAP."""
    replies: dict[str, dict] = {}
    imap_user = os.getenv("GMAIL_IMAP_USER", "").strip()
    app_pass  = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not imap_user or not app_pass:
        if verbose:
            print("  [imap] creds absent — skipping")
        return replies
    try:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(imap_user, app_pass)
        conn.select("INBOX")
        since_date = _parse_ts(cutoff_iso).strftime("%d-%b-%Y")
        _, msg_ids = conn.search(None, f'(UNSEEN SINCE "{since_date}")')
        id_list = msg_ids[0].split() if msg_ids[0] else []
        for mid in id_list[:100]:
            _, data = conn.fetch(mid, "(RFC822)")
            raw = data[0][1] if data and data[0] else b""
            msg = email_module.message_from_bytes(raw)
            from_addr = email_module.utils.parseaddr(msg.get("From", ""))[1].lower().strip()
            if from_addr not in target_emails:
                continue
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            replies[from_addr] = {
                "ts": _now_iso(), "label": "imap_unread", "body": body[:2000],
                "source": "gmail-imap",
                "raw": {"subject": msg.get("Subject", ""), "from": msg.get("From", "")},
            }
        conn.logout()
        if verbose and replies:
            print(f"  [imap] {len(replies)} unread from targets")
    except Exception as exc:
        if verbose:
            print(f"  [imap] error: {exc}")
    return replies


# ---------------------------------------------------------------------------
# 2c. reply-router.jsonl
# ---------------------------------------------------------------------------

def _load_reply_router(cutoff_iso: str) -> dict[str, dict]:
    replies: dict[str, dict] = {}
    path = DATA_ROOT / "operations" / "reply-router.jsonl"
    if not path.exists():
        return replies
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r  = json.loads(line)
            ts = r.get("ran_at", "")
            em = r.get("email", "").lower().strip()
            if ts >= cutoff_iso and em and em not in replies:
                replies[em] = {
                    "ts": ts, "label": r.get("label", ""),
                    "body": r.get("body", r.get("subject", "")),
                    "source": "reply-router", "raw": r,
                }
        except Exception:
            pass
    return replies


# ---------------------------------------------------------------------------
# 2d. mailbox/triage/inbound-*.jsonl
# ---------------------------------------------------------------------------

def _load_triage_files(cutoff_iso: str) -> dict[str, dict]:
    replies: dict[str, dict] = {}
    if not TRIAGE_DIR.exists():
        return replies
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    for day in (today, yesterday):
        tpath = TRIAGE_DIR / f"inbound-{day}.jsonl"
        if not tpath.exists():
            continue
        for line in tpath.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r  = json.loads(line)
                ts = r.get("classified_at") or r.get("ingested_at", "")
                em = r.get("from", "").lower().strip()
                if ts >= cutoff_iso and em and em not in replies:
                    replies[em] = {
                        "ts": ts, "label": r.get("classification", ""),
                        "body": r.get("body", r.get("subject", "")),
                        "source": f"inbound-{day}", "raw": r,
                    }
            except Exception:
                pass
    return replies


# ---------------------------------------------------------------------------
# 3. Auto-drafter — runtime.llm route='review' (router owns models + fallbacks)
# ---------------------------------------------------------------------------

def _generate_draft(wf: dict, reply_body: str, reply_label: str,
                    trigger: str, dry_run: bool, verbose: bool) -> dict | None:
    """Generate reply draft. trigger = 'reply' | 'opened_clicked' | 'opened'."""
    if dry_run:
        return {
            "draft_body": f"[DRY-RUN DRAFT — trigger={trigger}]\nRe: {wf['opener_subj']}\n\n"
                          f"Hey {wf['name']}, [Generated response would appear here]",
            "model": "dry-run", "dry_run": True,
        }

    if trigger == "opened_clicked":
        prompt = f"""You are Rick, AI CEO of meetrick.ai. 
{wf['name']} at {wf['company']} (ICP score: {wf['icp_score']}) opened AND clicked your cold email but hasn't replied yet.

ORIGINAL EMAIL SUBJECT: {wf['opener_subj']}
ORIGINAL EMAIL BODY:
{wf['opener_body'][:800]}

Pre-stage a short, punchy follow-up that:
- Acknowledges they found it interesting (no needy vibes)
- Adds one new hook or insight they didn't see in the original
- Ends with a low-friction next step (15-min call link or a single question)
- 3-4 sentences max. Signs off as Rick, AI CEO, meetrick.ai
- This is a PRE-STAGED DRAFT for Vlad's review — write the email body only."""

    else:  # trigger == 'reply'
        prompt = f"""You are Rick, AI CEO of meetrick.ai — sharp, warm, commercially serious.
Draft a reply to this cold-email response.

PROSPECT: {wf['name']} at {wf['company']} (ICP score: {wf['icp_score']})
ORIGINAL EMAIL SUBJECT: {wf['opener_subj']}
ORIGINAL EMAIL BODY:
{wf['opener_body'][:600]}

PROSPECT'S REPLY:
{reply_body[:1500]}
REPLY INTENT LABEL: {reply_label}

Draft a reply that:
- Acknowledges their specific message naturally (no "Great question!" opener)
- Moves toward a concrete next step (call booking, demo, or clarifying question)
- Is 3-5 sentences max — punchy, not corporate
- Signs off as Rick, AI CEO, meetrick.ai
- NO auto-send: this is for Vlad's review. Write the email body only.

If label is 'not_interested'/'unsubscribe': graceful 1-sentence close.
If label is 'sales_inquiry': move toward a 15-min call.
If label is 'question': answer directly + CTA."""

    from runtime.llm import generate_text
    result = generate_text("review", prompt, "")
    if result.mode != "live" or not result.content.strip():
        if verbose:
            print(f"  [draft] generate_text failed: mode={result.mode} notes={result.notes}")
        return None
    tokens = result.usage.input_tokens if result.usage else 0
    if verbose:
        print(f"  [draft] {result.model} generated ({tokens} input tokens)")
    return {"draft_body": result.content.strip(), "model": result.model, "prompt_tokens": tokens}


def _save_draft(wf_id: str, wf: dict, draft: dict, reply_info: dict, trigger: str) -> str:
    """Save draft → ~/rick-vault/mailbox/drafts/auto/{wf_id}_cw.json"""
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    path = DRAFT_DIR / f"{wf_id}_cw.json"
    payload = {
        "wf_id":           wf_id,
        "prospect_email":  wf["email"],
        "prospect_name":   wf["name"],
        "company":         wf["company"],
        "trigger":         trigger,
        "reply_body":      reply_info.get("body", ""),
        "reply_label":     reply_info.get("label", ""),
        "reply_ts":        reply_info.get("ts", ""),
        "reply_source":    reply_info.get("source", ""),
        "draft_body":      draft.get("draft_body", ""),
        "draft_subject":   f"Re: {wf['opener_subj']}",
        "model":           draft.get("model", ""),
        "review_required": True,
        "auto_send":       False,   # NEVER auto-send
        "created_at":      _now_iso(),
        "critical_window": True,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 4. P0 / P1 alert
# ---------------------------------------------------------------------------

def _fire_p0_alert(conn, wf: dict, reply_info: dict, draft_path: str,
                   dry_run: bool, verbose: bool) -> str:
    """P0: reply detected. Fire Telegram + notify_operator."""
    preview = reply_info.get("body", "")[:300].replace("\n", " ")
    text = (
        f"🚨 P0 REPLY — {wf['name']} @ {wf['company']} ({wf['email']})\n"
        f"Label: {reply_info.get('label','?')} | ICP: {wf['icp_score']}\n"
        f"Preview: {preview}\n"
        f"Draft staged: {Path(draft_path).name} (review required — NO auto-send)\n"
        f"wf_id: {wf['wf_id']} | critical_window=True"
    )
    if dry_run:
        print(f"  [DRY-RUN P0] {text}")
        return "dry-run"

    # Telegram path
    _send_telegram(text, verbose)

    # notify_operator_deduped path
    try:
        from runtime.engine import notify_operator_deduped
        result = notify_operator_deduped(
            conn, text,
            kind="critical_window_reply",
            dedup_window_hours=168,
            workflow_id=wf["wf_id"],
            lane="outreach",
            purpose="revenue",  # bypasses dedup
        )
        return result
    except ImportError:
        if verbose:
            print("  [alert] runtime.engine not importable — Telegram-only alert sent")
        return "telegram-only"


def _fire_p1_warm_alert(conn, wf: dict, resend_info: dict,
                         draft_path: str, dry_run: bool, verbose: bool) -> str:
    """P1: opened+clicked. Warm alert — pre-staged draft."""
    text = (
        f"🔥 WARM SIGNAL — {wf['name']} @ {wf['company']} ({wf['email']}) "
        f"OPENED + CLICKED. No reply yet.\n"
        f"Draft pre-staged: {Path(draft_path).name} (NOT sent)\n"
        f"resend_id={resend_info.get('resend_id','?')} | wf_id={wf['wf_id']}"
    )
    if dry_run:
        print(f"  [DRY-RUN P1] {text}")
        return "dry-run"

    _send_telegram(text, verbose)

    try:
        from runtime.engine import notify_operator_deduped
        return notify_operator_deduped(
            conn, text,
            kind="critical_window_warm",
            dedup_window_hours=24,
            workflow_id=wf["wf_id"],
            lane="outreach",
            purpose="ops",
        )
    except ImportError:
        return "telegram-only"


def _send_telegram(text: str, verbose: bool) -> None:
    """Send via runner.py telegram. Best-effort — never crash on failure."""
    try:
        chat_id = os.getenv("RICK_TELEGRAM_ALLOWED_CHAT_ID", "").strip()
        if not chat_id:
            if verbose:
                print("  [telegram] RICK_TELEGRAM_ALLOWED_CHAT_ID not set — skipping")
            return
        result = subprocess.run(
            ["python3", str(REPO_ROOT / "runtime" / "runner.py"),
             "telegram", "--text", text, "--chat-id", chat_id],
            capture_output=True, text=True, timeout=20,
        )
        if verbose:
            print(f"  [telegram] exit={result.returncode}")
    except Exception as exc:
        if verbose:
            print(f"  [telegram] error: {exc}")


# ---------------------------------------------------------------------------
# 5. Workflow stage update
# ---------------------------------------------------------------------------

def _mark_replied(conn, wf_id: str, dry_run: bool) -> None:
    if dry_run:
        return
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("UPDATE workflows SET stage='replied', updated_at=? WHERE id=?", (now, wf_id))
    conn.commit()


# ---------------------------------------------------------------------------
# 6. Digest update
# ---------------------------------------------------------------------------

def _update_critical_digest(wf_count: int, opens: int, replies: int, wfs: list[dict]) -> None:
    """Append critical-window line to today's reply-watch briefing and daily note."""
    today = datetime.now().strftime("%Y-%m-%d")
    digest_path = DATA_ROOT / "control" / "briefings" / f"reply-watch-{today}.md"
    now_str = _now_iso()
    
    # List wfs in window with status
    wf_lines = ""
    for w in wfs:
        elapsed = _elapsed_hours(w["last_touch_at"])
        remaining = max(0, CRITICAL_WINDOW_HOURS - elapsed)
        wf_lines += f"  - {w['name']} @ {w['company']} ({w['email']}) | touch+{elapsed:.0f}h | {remaining:.0f}h remain\n"

    line = (
        f"🚨 Critical window watch: {wf_count} wfs active, {opens} opens, {replies} replies last 72h\n"
        f"{wf_lines}"
    )
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    with digest_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{now_str}] {line}\n")

    # Append to daily note
    daily_note = DATA_ROOT / "memory" / f"{today}.md"
    if daily_note.exists():
        content = daily_note.read_text(encoding="utf-8")
        marker  = "## Critical Window Watch"
        section = f"\n{marker}\n- {line}"
        if marker not in content:
            with daily_note.open("a", encoding="utf-8") as fh:
                fh.write(section)
        else:
            lines = content.splitlines()
            for i in range(len(lines) - 1, -1, -1):
                if lines[i].startswith("- 🚨 Critical window"):
                    lines[i] = f"- {line.strip()}"
                    break
            daily_note.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 7. Flag health probe
# ---------------------------------------------------------------------------

def _probe_flag_health(wf_count: int, active_opens: int, active_replies: int) -> None:
    """Update RICK_CRITICAL_WINDOW_LIVE status in provider-health.json."""
    health_path = DATA_ROOT / "control" / "provider-health.json"
    health: dict = {}
    if health_path.exists():
        try:
            health = json.loads(health_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    live_flag = os.getenv("RICK_CRITICAL_WINDOW_LIVE", "0").strip() == "1"
    health["RICK_CRITICAL_WINDOW_LIVE"] = {
        "enabled":       live_flag,
        "wfs_in_window": wf_count,
        "opens_72h":     active_opens,
        "replies_72h":   active_replies,
        "last_probe":    _now_iso(),
        "status":        "active" if (live_flag and wf_count > 0) else ("standby" if wf_count > 0 else "idle"),
    }
    health_path.write_text(json.dumps(health, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Status command
# ---------------------------------------------------------------------------

def _show_status() -> None:
    state = _load_state()
    wfs_in = state.get("wfs_in_window", {})
    handled = state.get("handled_replies", {})
    p0_fired = state.get("p0_fired", {})
    print(f"critical-window-monitor STATUS @ {_now_iso()}")
    print(f"  last_run:       {state.get('last_run','never')}")
    print(f"  wfs_in_window:  {len(wfs_in)}")
    print(f"  handled_replies:{len(handled)}")
    print(f"  p0_fired:       {len(p0_fired)}")
    print(f"  LIVE flag:      {os.getenv('RICK_CRITICAL_WINDOW_LIVE','0') == '1'}")
    if wfs_in:
        print("\n  Active critical window wfs:")
        for wf_id, info in wfs_in.items():
            elapsed = _elapsed_hours(info.get("last_touch_at",""))
            remaining = max(0, CRITICAL_WINDOW_HOURS - elapsed)
            print(f"    {wf_id} | {info.get('email','?')} | touch+{elapsed:.0f}h | {remaining:.0f}h remain")
            print(f"      opens={info.get('opens_detected',0)} clicks={info.get('clicks_detected',0)} "
                  f"replied={info.get('replied',False)} draft={info.get('draft_staged',False)}")


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run(dry_run: bool = False, verbose: bool = False) -> dict:
    import sqlite3

    state = _load_state()
    handled_replies: dict[str, str] = state.get("handled_replies", {})
    p0_fired: dict[str, str]        = state.get("p0_fired", {})
    p1_fired: dict[str, str]        = state.get("p1_fired", {})
    wfs_in_window: dict[str, dict]  = state.get("wfs_in_window", {})

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        # ── 1. Load critical-window wfs ──────────────────────────────────
        wfs = _load_critical_wfs(conn)
        if not wfs:
            if verbose:
                print("critical-window-monitor: 0 wfs in critical window — nothing to do")
            _log({"ts": _now_iso(), "event": "no_critical_wfs"})
            _probe_flag_health(0, 0, 0)
            return {"wf_count": 0, "new_replies": 0, "new_opens": 0, "new_clicks": 0,
                    "total_opens_72h": 0, "total_replies_72h": 0}

        if verbose:
            print(f"Critical window: {len(wfs)} wfs")
            for w in wfs:
                elapsed = _elapsed_hours(w["last_touch_at"])
                print(f"  {w['wf_id']} | {w['email']} | {w['company']} | touch+{elapsed:.0f}h | "
                      f"expires {w['expires_at']}")

        # Update state with current wfs
        for wf in wfs:
            wid = wf["wf_id"]
            if wid not in wfs_in_window:
                wfs_in_window[wid] = {
                    "email":           wf["email"],
                    "name":            wf["name"],
                    "company":         wf["company"],
                    "last_touch_at":   wf["last_touch_at"],
                    "expires_at":      wf["expires_at"],
                    "opens_detected":  0,
                    "clicks_detected": 0,
                    "replied":         False,
                    "draft_staged":    False,
                    "entered_at":      _now_iso(),
                }

        email_to_wf  = {w["email"]: w for w in wfs}
        target_emails = set(email_to_wf.keys())
        cutoff_iso    = (datetime.now(timezone.utc) - timedelta(hours=REPLY_SCAN_HOURS)).isoformat(
            timespec="seconds"
        ) + "Z"

        # ── 2. Gather signals from all 4 sources ─────────────────────────
        resend_signals = _check_resend_events(wfs, verbose=verbose)
        reply_map      = _load_reply_router(cutoff_iso)
        reply_map.update(_load_triage_files(cutoff_iso))   # triage wins
        reply_map.update(_check_gmail_imap(target_emails, cutoff_iso, verbose=verbose))

        # ── 3. Process each wf ───────────────────────────────────────────
        new_replies = 0
        new_opens   = 0
        new_clicks  = 0

        for wf in wfs:
            wf_id = wf["wf_id"]
            em    = wf["email"]
            ws    = wfs_in_window[wf_id]  # state entry

            # ── A: Reply signal (highest priority) ────────────────────────
            reply_info = reply_map.get(em)
            already_handled = wf_id in handled_replies

            if reply_info and not already_handled:
                new_replies += 1
                ws["replied"] = True
                if verbose:
                    print(f"\n  🚨 REPLY: {em} ({wf['company']}) label={reply_info.get('label','?')}")

                # Draft (runtime.llm route='review'). A budget cap must never
                # kill the P0 alert below — draft is optional, alert is not.
                try:
                    draft = _generate_draft(wf, reply_info.get("body",""), reply_info.get("label",""),
                                            trigger="reply", dry_run=dry_run, verbose=verbose)
                except BudgetExceeded as exc:
                    print(f"  !! draft unavailable: LLM budget cap ({exc})", file=sys.stderr)
                    draft = None
                draft_path = ""
                if draft:
                    draft_path = _save_draft(wf_id, wf, draft, reply_info, trigger="reply")
                    ws["draft_staged"] = True
                    if verbose:
                        print(f"  Draft → {draft_path}")

                # P0 alert
                alert_result = _fire_p0_alert(conn, wf, reply_info, draft_path, dry_run, verbose)

                # Stop sequencer
                _mark_replied(conn, wf_id, dry_run)

                if not dry_run:
                    handled_replies[wf_id] = _now_iso()
                    p0_fired[wf_id]        = _now_iso()

                _log({
                    "ts": _now_iso(), "event": "critical_reply_detected",
                    "wf_id": wf_id, "email": em, "company": wf["company"],
                    "label": reply_info.get("label",""), "source": reply_info.get("source",""),
                    "draft_path": draft_path, "alert": alert_result, "dry_run": dry_run,
                })

            # ── B: Opened + Clicked (warm++ signal) ───────────────────────
            resend_info = resend_signals.get(em, {})
            opened  = resend_info.get("opened", False)
            clicked = resend_info.get("clicked", False)

            if opened:
                ws["opens_detected"] = ws.get("opens_detected", 0) + (0 if ws.get("opens_detected",0) > 0 else 1)
                if ws["opens_detected"] == 0:
                    new_opens += 1
            if clicked:
                ws["clicks_detected"] = ws.get("clicks_detected", 0) + (0 if ws.get("clicks_detected",0) > 0 else 1)

            # opened + clicked and not yet P1-alerted
            if opened and clicked and wf_id not in p1_fired and not reply_info:
                new_clicks += 1
                if verbose:
                    print(f"\n  🔥 WARM++: {em} opened+clicked (no reply yet)")

                # Pre-stage draft (optional — never let a budget cap crash the run)
                try:
                    draft = _generate_draft(wf, "", "", trigger="opened_clicked",
                                            dry_run=dry_run, verbose=verbose)
                except BudgetExceeded as exc:
                    print(f"  !! draft unavailable: LLM budget cap ({exc})", file=sys.stderr)
                    draft = None
                draft_path = ""
                if draft:
                    draft_path = _save_draft(wf_id, wf, draft, {}, trigger="opened_clicked")
                    ws["draft_staged"] = True

                _fire_p1_warm_alert(conn, wf, resend_info, draft_path, dry_run, verbose)

                if not dry_run:
                    p1_fired[wf_id] = _now_iso()

                _log({
                    "ts": _now_iso(), "event": "critical_warm_opened_clicked",
                    "wf_id": wf_id, "email": em, "company": wf["company"],
                    "resend_id": resend_info.get("resend_id",""),
                    "draft_path": draft_path, "dry_run": dry_run,
                })

            elif opened and not clicked and not reply_info:
                # Opened only — log warm signal, no alert
                _log({
                    "ts": _now_iso(), "event": "critical_opened_only",
                    "wf_id": wf_id, "email": em, "company": wf["company"],
                    "resend_id": resend_info.get("resend_id",""),
                })
                if verbose:
                    print(f"  👁 OPEN only: {em} (no click/reply yet)")

        # ── 4. Prune expired wfs from window state ────────────────────────
        expired_ids = [
            wid for wid, ws in wfs_in_window.items()
            if _elapsed_hours(ws.get("last_touch_at", "")) > CRITICAL_WINDOW_HOURS
        ]
        for wid in expired_ids:
            wfs_in_window.pop(wid, None)
            if verbose:
                print(f"  ⏱ De-escalated: {wid} (72h window closed)")
            _log({"ts": _now_iso(), "event": "deescalated", "wf_id": wid})

        # ── 5. Aggregate counters for digest ─────────────────────────────
        total_opens   = sum(ws.get("opens_detected", 0) for ws in wfs_in_window.values())
        total_replies = len(handled_replies)

        # ── 6. Update digest + daily note ────────────────────────────────
        if not dry_run:
            _update_critical_digest(len(wfs), total_opens, total_replies, wfs)

        # ── 7. Probe flag_health ─────────────────────────────────────────
        _probe_flag_health(len(wfs), total_opens, total_replies)

        # ── 8. Save state ─────────────────────────────────────────────────
        state["wfs_in_window"]    = wfs_in_window
        state["handled_replies"]  = handled_replies
        state["p0_fired"]         = p0_fired
        state["p1_fired"]         = p1_fired
        state["last_run"]         = _now_iso()
        _save_state(state, dry_run)

        summary = {
            "ts":          _now_iso(),
            "wf_count":    len(wfs),
            "new_replies": new_replies,
            "new_opens":   new_opens,
            "new_clicks":  new_clicks,
            "total_opens_72h":   total_opens,
            "total_replies_72h": total_replies,
            "dry_run":     dry_run,
        }
        _log({"event": "run_summary", **summary})
        return summary

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="critical-window-monitor: 5-min heightened reply detection for 72h post-touch"
    )
    parser.add_argument("--dry-run",  action="store_true", help="No DB writes / no alerts")
    parser.add_argument("--verbose",  "-v", action="store_true", help="Verbose output")
    parser.add_argument("--status",   action="store_true", help="Show current state and exit")
    args = parser.parse_args()

    if args.status:
        _show_status()
        return 0

    # Kill-switch gate
    live_flag = os.getenv("RICK_CRITICAL_WINDOW_LIVE", "0").strip() == "1"
    if not live_flag and not args.dry_run:
        print("[critical-window-monitor] RICK_CRITICAL_WINDOW_LIVE not set — forcing dry-run. "
              "Set RICK_CRITICAL_WINDOW_LIVE=1 to enable live alerts.")
        args.dry_run = True

    result = run(dry_run=args.dry_run, verbose=args.verbose)
    mode   = "[DRY-RUN] " if args.dry_run else ""
    print(
        f"{mode}critical-window-monitor | "
        f"wfs={result['wf_count']} | "
        f"new_replies={result['new_replies']} | "
        f"new_opens={result['new_opens']} | "
        f"new_clicks={result['new_clicks']} | "
        f"total_opens_72h={result['total_opens_72h']} | "
        f"total_replies_72h={result['total_replies_72h']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
