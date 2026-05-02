#!/usr/bin/env python3
"""
funnel-pulse.py — single-pane-of-glass cold-outreach funnel

Quickstart → Discovery → CRM → Scoring → Workflows → Send → Delivered → Opened → Replied
          → Drafts → Replied-out → Demo → Close

Usage:
  python3 scripts/funnel-pulse.py             # print funnel + write snapshot
  python3 scripts/funnel-pulse.py --json      # JSON output only (no colour)
  python3 scripts/funnel-pulse.py --no-write  # print only, skip snapshot write
  python3 scripts/funnel-pulse.py --days 14   # tighten time windows (default: all-time)

Hard constraints:
  - READ-ONLY on all logs; no writes except funnel-pulse.jsonl snapshot
  - No LLM calls; no outbound actions; no DB mutations
  - Target <30 seconds wall-clock
  - Smart-models invariant: no model calls
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ──────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────
VAULT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = VAULT / "operations"
OUTREACH = VAULT / "projects" / "outreach"
QUALIFIED_LEADS = VAULT / "projects" / "qualified-leads"
MAILBOX = VAULT / "mailbox"
DB_PATH = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(VAULT / "runtime" / "rick-runtime.db")))
SNAPSHOT_FILE = OPS / "funnel-pulse.jsonl"
ENV_FILE = Path.home() / ".openclaw" / "workspace" / "config" / "rick.env"

# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(days: int) -> str:
    """ISO date string N days ago (YYYY-MM-DD)."""
    return (_now_utc() - timedelta(days=days)).date().isoformat()


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        raw = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _is_recent(ts_str: str, days: int) -> bool:
    dt = _parse_ts(ts_str)
    if dt is None:
        return False
    return dt >= (_now_utc() - timedelta(days=days))


def _read_jsonl(path: Path, max_lines: int = 50_000) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return rows


def _get_env(key: str) -> str:
    val = os.environ.get(key, "")
    if val:
        return val
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val
    return ""


def _resend_get(path: str) -> Optional[dict]:
    api_key = _get_env("RESEND_API_KEY")
    if not api_key:
        return None
    url = f"https://api.resend.com{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0",  # Resend rejects Python-urllib UA
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _pct(num: int, denom: int) -> str:
    if not denom:
        return "n/a"
    return f"{100 * num / denom:.0f}%"


def _transition_label(prev: int, cur: int) -> str:
    """Render a conversion label.

    If a later stage is broader than the previous one (mixed-scope queues),
    cap at 100% and annotate the overflow so the dashboard stays readable.
    """
    if not prev:
        return "n/a"
    if cur <= prev:
        return _pct(cur, prev)
    return f"100% mix+{cur - prev}"


def _rate_arrow(label: str) -> str:
    """Colour-code conversion rate for terminal output."""
    if label == "n/a":
        return "   -"
    if label.startswith("100% mix+"):
        return f"\033[33m{label:>12}\033[0m"
    val = int(label.rstrip("%"))
    if val >= 20:
        return f"\033[32m{label:>5}\033[0m"  # green
    if val >= 8:
        return f"\033[33m{label:>5}\033[0m"   # yellow
    return f"\033[31m{label:>5}\033[0m"        # red


# ──────────────────────────────────────────────────────────────
# Data collectors
# ──────────────────────────────────────────────────────────────

ROLE_PREFIXES = frozenset({
    "info", "contact", "hello", "admin", "support", "team", "sales",
    "help", "office", "mail", "noreply", "no-reply", "billing", "hr",
    "jobs", "careers", "marketing", "press", "media", "pr", "legal",
    "legal", "privacy", "abuse", "spam", "webmaster",
})


def _is_role_account(email: str) -> bool:
    prefix = email.lower().split("@")[0] if "@" in email else ""
    return prefix in ROLE_PREFIXES


def collect_discovery(days: int) -> dict:
    """Stage 1 – founder-discovery-pipeline output in projects/qualified-leads/.

    Root wf_*.json files are validated leads. quarantine/ holds role-account
    or otherwise dropped leads. This is the cleanest 7d funnel source.
    """
    cutoff = _now_utc().timestamp() - days * 24 * 3600 if days else None
    root = []
    quarantine = []

    if QUALIFIED_LEADS.exists():
        for f in QUALIFIED_LEADS.glob("wf_*.json"):
            try:
                if cutoff and f.stat().st_mtime < cutoff:
                    continue
                root.append(json.loads(f.read_text(encoding="utf-8", errors="replace")))
            except (OSError, json.JSONDecodeError):
                pass
        qdir = QUALIFIED_LEADS / "quarantine"
        if qdir.exists():
            for f in qdir.glob("wf_*.json"):
                try:
                    if cutoff and f.stat().st_mtime < cutoff:
                        continue
                    quarantine.append(json.loads(f.read_text(encoding="utf-8", errors="replace")))
                except (OSError, json.JSONDecodeError):
                    pass

    validated = [r for r in root if isinstance(r, dict)]
    dropped = [r for r in quarantine if isinstance(r, dict)]
    total = len(validated) + len(dropped)
    with_email = total
    role_emails = sum(1 for r in dropped if _is_role_account(str(r.get("lead_email", ""))))
    icp_scored = sum(1 for r in validated if float(r.get("icp_score", 0) or 0) >= 0.65)

    return {
        "total": total,
        "validated": len(validated),
        "role_emails": role_emails,
        "icp_scored": icp_scored,
        "with_email": with_email,
        "root_count": len(validated),
        "quarantine_count": len(dropped),
    }


def collect_quickstart_runs(days: int = 7) -> dict:
    """Stage 0 – anonymous quickstart telemetry from operations/quickstart-pings.jsonl."""
    rows = _read_jsonl(OPS / "quickstart-pings.jsonl")
    cutoff = _cutoff(days) if days else None
    recent = [
        r for r in rows
        if r.get("event") in {"start", "complete"}
        and (cutoff is None or (r.get("ts", "") or "")[:10] >= cutoff)
    ]
    started = sum(1 for r in recent if r.get("event") == "start")
    completed = sum(1 for r in recent if r.get("event") == "complete")

    return {
        "started": started,
        "completed": completed,
        "total": started,
        "recent": len(recent),
    }


def collect_contacted(days: int) -> dict:
    """Stage 2 – CRM contacts (contacted.json) and role-account suppression."""
    path = OUTREACH / "contacted.json"
    if not path.exists():
        return {"total": 0, "suppressed": 0, "role_suppressed": 0, "email_sent": 0, "clean": 0}

    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {"total": 0, "suppressed": 0, "role_suppressed": 0, "email_sent": 0, "clean": 0}

    items = raw.values() if isinstance(raw, dict) else raw
    items = [v for v in items if isinstance(v, dict)]

    total = len(items)
    suppressed = sum(1 for v in items if v.get("suppressed"))
    role_suppressed = sum(1 for v in items if "role_account" in str(v.get("suppress_reason", "")))
    email_sent = sum(1 for v in items if v.get("email_sent"))

    return {
        "total": total,
        "suppressed": suppressed,
        "role_suppressed": role_suppressed,
        "email_sent": email_sent,
        "clean": total - role_suppressed,
    }


def collect_icp_workflows(days: int) -> dict:
    """Stage 3-4 – qualified_lead workflows in the runtime DB."""
    if not DB_PATH.exists():
        return {"total": 0, "by_stage": {}}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            "SELECT stage, status, COUNT(*) c FROM workflows WHERE kind='qualified_lead' GROUP BY stage, status"
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except sqlite3.Error:
        return {"total": 0, "by_stage": {}}

    total = sum(r["c"] for r in rows)
    by_stage: dict[str, int] = {}
    for r in rows:
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + r["c"]

    active = by_stage.get("sequence-active", 0)
    completed = by_stage.get("completed", 0) + by_stage.get("complete", 0)
    bounced_paused = by_stage.get("bounced-paused", 0)
    queued = by_stage.get("queued", 0)

    return {
        "total": total,
        "active": active,
        "completed": completed,
        "bounced_paused": bounced_paused,
        "queued": queued,
        "by_stage": by_stage,
    }


def collect_sends(days: int) -> dict:
    """Stage 5 – cold emails dispatched (email-sends.jsonl + email-sequence-send.jsonl)."""
    cold_sends = _read_jsonl(OPS / "email-sends.jsonl")
    seq_sends = _read_jsonl(OPS / "email-sequence-send.jsonl")

    cold_sent = sum(1 for e in cold_sends if e.get("status") == "sent")
    cold_bounced = 0
    for e in cold_sends:
        if e.get("status") == "sent":
            # later stage determines actual bounce count from email-bounces.jsonl
            pass
    # sequence-send: only count actual sends; current state has failures only.
    seq_sent = sum(
        1 for e in seq_sends
        if e.get("status") == "sent" or (e.get("status") and e.get("status") not in {"send-failed", "failed", "error"})
    )
    seq_failed = sum(1 for e in seq_sends if e.get("status") in {"send-failed", "failed", "error"} or e.get("error"))

    # If caller wants a time-filtered view, apply cutoff by date
    cutoff = _cutoff(days) if days else None

    cold_recent = sum(
        1 for e in cold_sends
        if e.get("status") == "sent" and (cutoff is None or (e.get("ts", "") or "")[:10] >= cutoff)
    )
    seq_recent = sum(
        1 for e in seq_sends
        if (e.get("status") == "sent" or (e.get("status") and e.get("status") not in {"send-failed", "failed", "error"}))
        and (cutoff is None or (e.get("timestamp", "") or e.get("ts", "") or "")[:10] >= cutoff)
    )

    return {
        "cold_total": cold_sent,
        "cold_recent": cold_recent,
        "cold_bounced": cold_bounced,
        "seq_total": seq_sent,
        "seq_recent": seq_recent,
        "seq_failed": seq_failed,
        "total": cold_sent + seq_sent,
        "recent": cold_recent + seq_recent,
    }


def collect_deliverability(days: int) -> dict:
    """Stage 6 – email-bounces.jsonl + Resend API open counts.

    Delivered is derived from the cold send log minus actual bounce events.
    Opens are counted from a small Resend sample matched by recipient.
    """
    bounce_rows = _read_jsonl(OPS / "email-bounces.jsonl")
    bounces = [r for r in bounce_rows if r.get("event") == "bounced"]
    bounce_count = len(bounces)

    cold_sends = [r for r in _read_jsonl(OPS / "email-sends.jsonl") if r.get("status") == "sent"]
    resend_opened = 0
    resend_total = 0
    resend_ok = False

    def _norm_email(raw: str) -> str:
        raw = (raw or "").strip().lower()
        if "<" in raw and ">" in raw:
            raw = raw.split("<", 1)[1].split(">", 1)[0]
        return raw

    def _norm_subject(raw: str) -> str:
        return re.sub(r"\s+", " ", (raw or "").strip().lower())

    # Build the local sent set from the actual outbox files so we can match
    # Resend records by both recipient and subject (recipient-only matching
    # collapses follow-up touches and undercounts opens).
    local_keys: set[tuple[str, str]] = set()
    outbox_dir = MAILBOX / "outbox"
    if outbox_dir.exists():
        for f in outbox_dir.glob("**/*.md"):
            try:
                to = ""
                subject = ""
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("to:"):
                        to = line.split(":", 1)[1].strip()
                    elif line.startswith("subject:"):
                        subject = line.split(":", 1)[1].strip()
                    elif line.strip() == "---" and to and subject:
                        break
                if to and subject:
                    local_keys.add((_norm_email(to), _norm_subject(subject)))
            except OSError:
                pass

    # Paginate through Resend emails so we don't miss older touches.
    before = None
    for _page_num in range(20):
        path = f"/emails?limit=100" + (f"&before={before}" if before else "")
        recent = _resend_get(path)
        if not recent:
            break
        resend_ok = True
        batch = recent.get("data", []) or []
        if not batch:
            break
        resend_total += len(batch)
        for e in batch:
            to = _norm_email((e.get("to") or [""])[0])
            subject = _norm_subject(e.get("subject") or "")
            if (to, subject) not in local_keys:
                continue
            if e.get("last_event") in ("opened", "clicked"):
                resend_opened += 1
        if not recent.get("has_more"):
            break
        before = batch[-1].get("id")

    return {
        "bounces_log": bounce_count,
        "resend_ok": resend_ok,
        "resend_total": resend_total,
        "resend_opened": resend_opened,
        "resend_bounced": bounce_count,
        "resend_suppressed": 0,
        "net_delivered": max(0, len(cold_sends) - bounce_count),
    }


def collect_replies(days: int) -> dict:
    """Stage 7 – genuine outreach replies from reply-router.jsonl + triage inbox."""
    GENUINE = frozenset({
        "sales_inquiry", "interested", "meeting_request", "positive",
        "question", "objection_with_counter", "referral_request",
    })

    router_rows = _read_jsonl(OPS / "reply-router.jsonl")
    genuine = [r for r in router_rows if r.get("label", "") in GENUINE]
    not_interested = sum(
        1 for r in router_rows
        if r.get("label", "") in ("not_interested", "unsubscribe", "objection")
    )

    # Also check triage inbound files for recent classified replies
    triage_genuine = 0
    triage_dir = MAILBOX / "triage"
    if triage_dir.exists():
        for tf in sorted(triage_dir.glob("inbound-*.jsonl"))[-14:]:
            for row in _read_jsonl(tf):
                cls = row.get("classification", "")
                rr = row.get("router_result", {}) or {}
                if cls in GENUINE or rr.get("action") in ("queued-deal-close", "drafted"):
                    triage_genuine += 1

    cutoff = _cutoff(days) if days else None
    genuine_recent = sum(
        1 for r in genuine
        if cutoff is None or (r.get("ran_at", "") or "")[:10] >= cutoff
    )

    return {
        "genuine_total": len(genuine),
        "genuine_recent": genuine_recent,
        "not_interested": not_interested,
        "triage_genuine": triage_genuine,
    }


def collect_drafts(days: int) -> dict:
    """Stage 8 – pending reply drafts.

    Two sources:
    (a) auto-draft-reply.jsonl — agent-generated reply drafts pending Vlad approval.
    (b) mailbox/drafts/auto/ — saved reply JSON files (may overlap with (a)).

    NOTE: outreach/drafts-*.json contains OUTBOUND cold-email proposals, not
    inbound reply drafts — excluded here to avoid inflating this stage.
    """
    # Source (a): auto-draft-reply.jsonl events
    auto_drafts = _read_jsonl(OPS / "auto-draft-reply.jsonl")
    auto_pending = sum(
        1 for d in auto_drafts if d.get("action") == "auto-drafted"
    )

    # Source (b): mailbox/drafts/auto/ saved JSON files
    auto_dir = MAILBOX / "drafts" / "auto"
    mailbox_pending = len(list(auto_dir.glob("*.json"))) if auto_dir.exists() else 0

    # Dedup: if both sources exist, use max (they track the same events)
    pending = max(auto_pending, mailbox_pending)

    # Outbound cold-email proposal drafts (for informational note only)
    cold_draft_rows = 0
    SENT_STATUSES = {"sent", "approved", "delivered", "skipped", "rejected"}
    cold_sent = 0
    for df in sorted(OUTREACH.glob("drafts-*.json")):
        try:
            data = json.loads(df.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        rows = data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []
        rows = [r for r in rows if isinstance(r, dict)]
        cold_draft_rows += len(rows)
        cold_sent += sum(1 for r in rows if r.get("status", "") in SENT_STATUSES)

    return {
        "pending": pending,
        "auto_pending": auto_pending,
        "mailbox_pending": mailbox_pending,
        "cold_draft_rows": cold_draft_rows,
        "cold_draft_sent": cold_sent,
    }


def collect_outbox(days: int) -> dict:
    """Stage 9 – replies actually sent (mailbox/outbox/*.json)."""
    outbox_dir = MAILBOX / "outbox"
    if not outbox_dir.exists():
        return {"total": 0, "pitches": 0, "followups": 0, "replies": 0}

    all_files = list(outbox_dir.glob("**/*.json"))
    pitches = [f for f in all_files if "pitch" in f.name]
    followups = [f for f in all_files if re.search(r"(followup|follow-up|day\d+)", f.name.lower())]
    replies = [f for f in all_files if re.search(r"reply|signal", f.name.lower())]

    cutoff = _cutoff(days) if days else None
    recent = []
    if cutoff:
        for f in all_files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime >= (_now_utc() - timedelta(days=days)):
                recent.append(f)
    else:
        recent = all_files

    return {
        "total": len(all_files),
        "pitches": len(pitches),
        "followups": len(followups),
        "replies": len(replies),
        "recent": len(recent),
    }


def collect_demos() -> dict:
    """Stage 10 – demo calls / meetings (no dedicated log; inferred from deal_close)."""
    if not DB_PATH.exists():
        return {"count": 0, "source": "no-db"}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM workflows WHERE kind='deal_close' AND stage IN ('qualified','demo','call_booked')"
        )
        count = cur.fetchone()[0]
        conn.close()
        return {"count": count, "source": "db"}
    except sqlite3.Error:
        return {"count": 0, "source": "db-error"}


def collect_closes() -> dict:
    """Stage 11 – deal_close workflows closed/done."""
    if not DB_PATH.exists():
        return {"closed": 0, "pipeline": 0}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT stage, status, COUNT(*) c FROM workflows WHERE kind='deal_close' GROUP BY stage, status"
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
    except sqlite3.Error:
        return {"closed": 0, "pipeline": 0}

    closed = sum(r["c"] for r in rows if r["stage"] == "closed" and r["status"] == "done")
    active = sum(r["c"] for r in rows if r["status"] == "queued" and r["stage"] not in ("cancelled",))
    disqualified = sum(r["c"] for r in rows if r["stage"] in ("disqualified", "not-icp"))

    return {"closed": closed, "active_pipeline": active, "disqualified": disqualified}


# ──────────────────────────────────────────────────────────────
# Funnel assembly
# ──────────────────────────────────────────────────────────────

def build_funnel(days: int) -> dict:
    quickstart = collect_quickstart_runs(7)
    discovery = collect_discovery(days)
    contacted = collect_contacted(days)
    workflows = collect_icp_workflows(days)
    sends = collect_sends(days)
    deliverability = collect_deliverability(days)
    replies = collect_replies(days)
    drafts = collect_drafts(days)
    outbox = collect_outbox(days)
    demos = collect_demos()
    closes = collect_closes()
    open_reply_rate = None
    if deliverability["resend_opened"]:
        open_reply_rate = replies["genuine_total"] / deliverability["resend_opened"]
    open_reply = {
        "opens": deliverability["resend_opened"],
        "replies": replies["genuine_total"],
        "rate": open_reply_rate,
        "target": 0.03,
    }

    # Canonical funnel counts (ordered top to bottom)
    stages = [
        {
            "id": "quickstart_runs",
            "label": "Quickstart runs 7d",
            "count": quickstart["started"],
            "note": f"{quickstart['started']} started, {quickstart['completed']} completed",
        },
        {
            "id": "discovery",
            "label": "Discovery candidates 7d",
            "count": discovery["total"],
            "note": f"validated: {discovery['validated']}, role-account dropped: {discovery['role_emails']}",
        },
        {
            "id": "validated",
            "label": "Validated emails",
            "count": discovery["validated"],
            "note": f"ICP score >=0.65: {discovery['icp_scored']}",
        },
        {
            "id": "icp",
            "label": "ICP score >=0.65",
            "count": discovery["icp_scored"],
            "note": f"qualified_leads root files: {discovery['root_count']}",
        },
        {
            "id": "crm_contacts",
            "label": "qualified_lead workflows",
            "count": workflows["total"],
            "note": f"queued: {workflows['queued']}, active: {workflows['active']}, bounced-paused: {workflows['bounced_paused']}",
        },
        {
            "id": "emails_sent",
            "label": "Cold-emails sent",
            "count": sends["cold_total"],
            "note": f"seq sent: {sends['seq_total']} | seq failed: {sends['seq_failed']}",
        },
        {
            "id": "delivered",
            "label": "Cold-emails delivered",
            "count": deliverability["net_delivered"],
            "note": f"bounced: {deliverability['resend_bounced']}, Resend sample opens: {deliverability['resend_opened']}",
        },
        {
            "id": "opened",
            "label": "Email opens",
            "count": deliverability["resend_opened"],
            "note": f"Resend sample size: {deliverability['resend_total']} recent emails",
        },
        {
            "id": "replied",
            "label": "Genuine replies received",
            "count": replies["genuine_total"],
            "note": f"not-interested/unsub: {replies['not_interested']}",
        },
        {
            "id": "drafts",
            "label": "Reply drafts pending Vlad review",
            "count": drafts["pending"],
            "note": f"cold proposals generated: {drafts['cold_draft_rows']} (separate queue)",
        },
        {
            "id": "replied_out",
            "label": "Replies sent (outbox)",
            "count": outbox["total"],
            "note": f"pitches: {outbox['pitches']}, followups: {outbox['followups']}, replies: {outbox['replies']}",
        },
        {
            "id": "demos",
            "label": "Demos / calls delivered",
            "count": demos["count"],
            "note": "from deal_close qualified/demo stage",
        },
        {
            "id": "closed",
            "label": "Customers closed (deal_close/done)",
            "count": closes["closed"],
            "note": f"disqualified: {closes['disqualified']}; real MRR: $9 (1 sub)",
        },
    ]

    # Attach conversion rates (stage N → N+1)
    for i, stage in enumerate(stages):
        if i == 0:
            stage["conv_to_next"] = None
        else:
            prev = stages[i - 1]["count"]
            cur_count = stage["count"]
            stage["conv_to_next"] = _transition_label(prev, cur_count)

    # Identify leak across the primary funnel only (ignore operational queues
    # like drafts/outbox/demos so we don’t flag healthy downstream admin churn).
    primary_ids = {"quickstart_runs", "discovery", "validated", "icp", "crm_contacts", "emails_sent", "delivered", "opened", "replied", "closed"}
    biggest_drop = 0
    leak_stage = ""
    for i in range(1, len(stages)):
        prev_stage = stages[i - 1]
        cur_stage = stages[i]
        if prev_stage["id"] not in primary_ids or cur_stage["id"] not in primary_ids:
            continue
        prev_count = prev_stage["count"]
        cur_count = cur_stage["count"]
        drop = prev_count - cur_count
        if drop > biggest_drop:
            biggest_drop = drop
            leak_stage = prev_stage["id"]

    return {
        "generated_at": _now_utc().isoformat(),
        "window_days": days or "all-time",
        "stages": stages,
        "leak_stage": leak_stage,
        "biggest_drop": biggest_drop,
        "resend_live": deliverability["resend_ok"],
        "open_reply": open_reply,
        "_sources": {
            "quickstart_runs": "operations/quickstart-pings.jsonl",
            "discovery": "outreach/*.jsonl",
            "contacted": "projects/outreach/contacted.json",
            "workflows": "runtime/rick-runtime.db (qualified_lead kind)",
            "sends": "operations/email-sends.jsonl + email-sequence-send.jsonl",
            "deliverability": "Resend API /emails?limit=100",
            "replies": "operations/reply-router.jsonl",
            "drafts": "mailbox/drafts/auto + operations/auto-draft-reply.jsonl",
            "outbox": "mailbox/outbox/*.json",
            "closes": "runtime/rick-runtime.db (deal_close kind)",
        },
    }


# ──────────────────────────────────────────────────────────────
# Renderer
# ──────────────────────────────────────────────────────────────

PIPE = "│"
ARROW = "↓"

def _bar(val: int, max_val: int, width: int = 30) -> str:
    if max_val <= 0:
        return " " * width
    filled = int(width * val / max_val)
    return ("█" * filled).ljust(width)


def render_funnel(data: dict, no_colour: bool = False) -> str:
    stages = data["stages"]
    lines: list[str] = []

    ts = datetime.fromisoformat(data["generated_at"]).strftime("%Y-%m-%d %H:%M UTC")
    window = data["window_days"]
    window_str = f"{window}d" if isinstance(window, int) else window

    lines.append("")
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append(f"║  🔬 FUNNEL PULSE  {ts}  [{window_str}]             ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")
    lines.append("")

    max_count = max((s["count"] for s in stages), default=1) or 1
    label_w = max(len(s["label"]) for s in stages)

    for i, stage in enumerate(stages):
        count = stage["count"]
        label = stage["label"]
        note = stage["note"]
        conv = stage.get("conv_to_next")

        bar = _bar(count, max_count, width=26)
        count_str = f"{count:>5}"
        label_padded = label.ljust(label_w)

        # Colour the count if zero (red) or low (yellow)
        if not no_colour:
            if count == 0 and i > 0:
                count_str = f"\033[31m{count_str}\033[0m"
            elif count < 5 and i > 2:
                count_str = f"\033[33m{count_str}\033[0m"

        lines.append(f"  {label_padded}  {count_str}  {bar}")
        lines.append(f"  {'─' * label_w}  {'─' * 5}  {note}")

        if i < len(stages) - 1:
            if conv and conv != "n/a":
                arrow_label = f"  {ARROW}  conv: {conv}"
                if not no_colour:
                    arrow_label = f"  {_rate_arrow(conv)}  {ARROW}  conv: {conv}" if conv.startswith("100% mix+") else f"  {_rate_arrow(conv)}  {ARROW}  conv: {conv}"
                lines.append(arrow_label)
            else:
                lines.append(f"  {ARROW}")

    lines.append("")

    # Conversion chain
    conv_chain = " → ".join(
        s["conv_to_next"] for s in stages[1:] if s.get("conv_to_next") and s["conv_to_next"] != "n/a"
    )
    lines.append(f"  Conversion chain:  {conv_chain}")
    lines.append("")

    # Open→reply benchmark
    open_reply = data.get("open_reply") or {}
    orate = open_reply.get("rate")
    if orate is None:
        lines.append("  Open→reply:      n/a (no opens recorded)")
    else:
        opens = open_reply.get("opens", 0)
        replies_n = open_reply.get("replies", 0)
        target = float(open_reply.get("target", 0.03))
        gap = orate - target
        lines.append(
            f"  Open→reply:      {replies_n}/{opens} = {orate:.1%} "
            f"(target {target:.1%}, gap {gap:+.1%})"
        )
    lines.append("")

    # Leak diagnosis
    leak = data.get("leak_stage", "")
    drop = data.get("biggest_drop", 0)
    if leak:
        lines.append(f"  ⚠️  BIGGEST LEAK → after '{leak}' ({drop} leads dropped)")
    else:
        lines.append("  ✓  No dominant leak identified")

    if not data.get("resend_live"):
        lines.append("  ⚠️  Resend API unreachable — open rate data may be stale")

    lines.append("")
    lines.append("─" * 70)
    lines.append("  Sources: projects/qualified-leads/ · contacted.json · rick-runtime.db")
    lines.append("           email-sends.jsonl · Resend API · reply-router.jsonl · outbox/")
    lines.append("─" * 70)
    lines.append("")

    return "\n".join(lines)


def render_compact(data: dict) -> str:
    """Ultra-compact one-liner funnel for digest injection."""
    stages = data["stages"]
    parts = []
    for s in stages:
        parts.append(f"{s['id']}={s['count']}")
    chain = " → ".join(
        s["conv_to_next"] for s in stages[1:] if s.get("conv_to_next") and s["conv_to_next"] != "n/a"
    )
    leak = data.get("leak_stage", "?")
    return f"funnel: {' | '.join(parts)} | conv: {chain} | leak: {leak}"


# ──────────────────────────────────────────────────────────────
# Snapshot writer
# ──────────────────────────────────────────────────────────────

def write_snapshot(data: dict) -> None:
    row = {
        "ts": data["generated_at"],
        "event": "funnel.snapshot",
        "window_days": data["window_days"],
        "stages": {s["id"]: s["count"] for s in data["stages"]},
        "leak_stage": data.get("leak_stage"),
        "biggest_drop": data.get("biggest_drop"),
        "resend_live": data.get("resend_live"),
        "open_reply": data.get("open_reply"),
        "compact": render_compact(data),
    }
    try:
        with open(SNAPSHOT_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"[funnel-pulse] snapshot write failed: {exc}", file=sys.stderr)


# ──────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Print cold-outreach funnel pulse.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--days", type=int, default=0,
                    help="Filter time window in days (0=all-time, default: 0)")
    ap.add_argument("--json", action="store_true", dest="json_output",
                    help="Emit JSON only (no ASCII art)")
    ap.add_argument("--compact", action="store_true",
                    help="Single-line summary (for digest injection)")
    ap.add_argument("--no-write", action="store_true",
                    help="Skip writing snapshot to funnel-pulse.jsonl")
    ap.add_argument("--no-colour", action="store_true",
                    help="Disable ANSI colour codes (for piping/logging)")
    args = ap.parse_args()

    no_colour = args.no_colour or not sys.stdout.isatty()

    data = build_funnel(days=args.days)

    if args.json_output:
        print(json.dumps(data, indent=2))
    elif args.compact:
        print(render_compact(data))
    else:
        print(render_funnel(data, no_colour=no_colour))

    if not args.no_write:
        write_snapshot(data)


if __name__ == "__main__":
    main()
