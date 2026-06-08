#!/usr/bin/env python3
"""
funnel-pulse.py — single-pane-of-glass cold-outreach funnel

Quickstart → Discovery → CRM → Scoring → Workflows → Send → Delivered → Opened → Replied
          → Drafts → Replied-out → Demo → Close

Partitioned view (default on):
  - Pre-2026-05-04 (legacy SMB spray)   — informational only
  - Post-2026-05-04 (ICP multi-touch)   — per-stage signal we care about

Usage:
  python3 scripts/funnel-pulse.py             # print partition + funnel + write snapshot
  python3 scripts/funnel-pulse.py --json      # JSON output only (no colour)
  python3 scripts/funnel-pulse.py --no-write  # print only, skip snapshot write
  python3 scripts/funnel-pulse.py --days 14   # tighten time windows (default: all-time)
  python3 scripts/funnel-pulse.py --no-partition  # skip partition block (legacy mode)

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
# Partition constants
# ──────────────────────────────────────────────────────────────
# Funnel is split here: everything before is legacy SMB spray;
# everything from this date forward is the new ICP multi-touch pipeline.
ICP_PARTITION_DATE = "2026-05-04"  # YYYY-MM-DD

# Strategy B promise (from outreach strategy doc)
STRATEGY_B_MANAGED_CLOSE_PER_MONTH_FROM = 3   # months from launch
STRATEGY_B_MANAGED_CLOSE_TARGET = 1            # closes/month at month 3
STRATEGY_B_MANAGED_MRR = 499                   # $/close
STRATEGY_B_LAUNCH_DATE = "2026-04-27"          # ICP sequence start date

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
# ICP Partition collectors
# ──────────────────────────────────────────────────────────────

_RESEND_ICP_CACHE: Optional[list] = None  # avoid double-fetching


def _real_vault() -> Path:
    """Resolve the actual operational vault path.

    RICK_DATA_ROOT may point at an install-test dir that lacks ICP pipeline
    artifacts. Resolve by picking the candidate vault whose sequencer.jsonl
    is largest on disk (i.e. the real operational log, not an empty test copy).
    """
    candidates = [
        VAULT,                           # RICK_DATA_ROOT or default
        Path.home() / "rick-vault",      # canonical fallback
    ]
    best: Optional[Path] = None
    best_size = -1
    for c in candidates:
        seq = c / "operations" / "sequencer.jsonl"
        if seq.exists():
            try:
                sz = seq.stat().st_size
            except OSError:
                sz = 0
            if sz > best_size:
                best_size = sz
                best = c
    return best if best is not None else VAULT


def _minutes_ago(ts_str: str) -> Optional[float]:
    """Return how many minutes ago a timestamp was (float). None if unparseable."""
    dt = _parse_ts(ts_str)
    if dt is None:
        return None
    return (_now_utc() - dt).total_seconds() / 60


def _human_ago(ts_str: str) -> str:
    mins = _minutes_ago(ts_str)
    if mins is None:
        return "?"
    if mins < 2:
        return "just now"
    if mins < 60:
        return f"{int(mins)} min ago"
    hours = mins / 60
    if hours < 24:
        return f"{hours:.1f}h ago"
    return f"{hours / 24:.1f}d ago"


def _colour_rate(val: float, green: float, yellow: float, label: str, no_colour: bool = False) -> str:
    """Colour-code a rate against green/yellow thresholds (higher=better)."""
    if no_colour:
        return label
    if val >= green:
        return f"\033[32m{label}\033[0m"  # green
    if val >= yellow:
        return f"\033[33m{label}\033[0m"  # yellow
    return f"\033[31m{label}\033[0m"       # red


def collect_icp_leads_from_vault() -> list[dict]:
    """Return all qualified_lead JSON files from projects/qualified-leads/ (not quarantine)."""
    rv = _real_vault()
    ql = rv / "projects" / "qualified-leads"
    leads: list[dict] = []
    if not ql.exists():
        return leads
    for f in ql.glob("wf_*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            if isinstance(d, dict):
                leads.append(d)
        except (OSError, json.JSONDecodeError):
            pass
    return leads


def collect_sequencer_events() -> dict:
    """Parse sequencer.jsonl for ICP touch events per workflow."""
    rv = _real_vault()
    rows = _read_jsonl(rv / "operations" / "sequencer.jsonl")
    dispatched: dict[str, list] = {}    # wf_id -> list of dispatch events
    deferred_kinds: dict[str, set] = {} # wf_id -> set of deferred kinds
    for r in rows:
        wid = r.get("wf_id", "")
        if not wid:
            continue
        evt = r.get("event", "")
        if evt == "touch_dispatched":
            dispatched.setdefault(wid, []).append(r)
        elif evt == "touch_deferred":
            deferred_kinds.setdefault(wid, set()).add(r.get("kind", ""))
    return {"dispatched": dispatched, "deferred_kinds": deferred_kinds}


def collect_day0_icp_status() -> dict[str, dict]:
    """Return the latest per-lead Day-0 status from day0-fire-monitor.jsonl.

    Returns dict keyed by wf_id with fields: sent, bounced, replied, job_id.
    Uses the most-recent monitor snapshot that has per_lead data.
    """
    rv = _real_vault()
    rows = _read_jsonl(rv / "operations" / "day0-fire-monitor.jsonl")
    # Find the latest snapshot with non-empty per_lead
    latest: Optional[dict] = None
    for r in reversed(rows):
        if r.get("per_lead"):
            latest = r
            break
    if latest is None:
        return {}
    result: dict[str, dict] = {}
    for lead in latest["per_lead"]:
        wid = lead.get("wf_id", "")
        if wid:
            result[wid] = {
                "job_id": lead.get("job_id"),
                "email": lead.get("email"),
                "lead_title": lead.get("lead_title", ""),
                "sent": lead.get("sent", False),
                "bounced": lead.get("bounced", False),
                "replied": lead.get("replied", False),
                "reply_label": lead.get("reply_label"),
            }
    return result


def collect_icp_resend_events(icp_emails: list[str]) -> dict[str, dict]:
    """Look up recent Resend events for a set of ICP lead email addresses.

    Returns dict keyed by lowercase email with Resend event metadata.
    Result cached in module-level var to avoid double API calls.
    """
    global _RESEND_ICP_CACHE

    target_set = {e.lower() for e in icp_emails if e}
    if not target_set:
        return {}

    # Fetch recent Resend emails (up to 200, 2 pages)
    if _RESEND_ICP_CACHE is None:
        collected: list[dict] = []
        before = None
        for _ in range(2):
            path = "/emails?limit=100" + (f"&before={before}" if before else "")
            resp = _resend_get(path)
            if not resp:
                break
            batch = resp.get("data") or []
            collected.extend(batch)
            if not resp.get("has_more") or not batch:
                break
            before = batch[-1].get("id")
        _RESEND_ICP_CACHE = collected

    result: dict[str, dict] = {}
    for e in _RESEND_ICP_CACHE:
        to_list = e.get("to") or []
        for recipient in to_list:
            email_lower = _norm_email(recipient).lower()
            if email_lower in target_set:
                last_event = e.get("last_event")
                created_at = e.get("created_at", "")
                result[email_lower] = {
                    "resend_id": e.get("id"),
                    "subject": e.get("subject", ""),
                    "last_event": last_event,
                    "created_at": created_at,
                    "human_ago": _human_ago(created_at),
                }
    return result


def _norm_email(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if "<" in raw and ">" in raw:
        raw = raw.split("<", 1)[1].split(">", 1)[0]
    return raw


def build_icp_partition() -> dict:
    """Assemble the Post-ICP_PARTITION_DATE multi-touch funnel partition."""
    icp_leads = collect_icp_leads_from_vault()
    sequencer = collect_sequencer_events()
    day0_status = collect_day0_icp_status()

    # Gather all ICP emails for Resend lookup
    icp_emails = [lead.get("lead_email", "") for lead in icp_leads if lead.get("lead_email")]
    resend_events = collect_icp_resend_events(icp_emails)

    # --- Day-0 cold sent ---
    day0_sent_count = sum(1 for v in day0_status.values() if v.get("sent"))
    day0_total_tracked = len(day0_status)
    day0_bounced = sum(1 for v in day0_status.values() if v.get("bounced"))
    # Delivery rate from Resend for ICP emails
    icp_resend_delivered = sum(
        1 for e in resend_events.values()
        if (e.get("last_event") or "") in {"delivered", "opened", "clicked"}
    )
    icp_resend_bounced = sum(
        1 for e in resend_events.values()
        if (e.get("last_event") or "") in {"bounced", "suppressed"}
    )
    icp_resend_total = len(resend_events)
    delivery_rate = (
        icp_resend_delivered / icp_resend_total * 100
        if icp_resend_total > 0 else None
    )

    # --- Day-3 voice ---
    # From sequencer: voice step deferred = reason awaiting_sent_email_cold_1 → skipped
    day3_voice_dispatched: list[dict] = []
    day3_voice_skipped_wf: set[str] = set()
    for wid, dispatch_list in sequencer["dispatched"].items():
        for ev in dispatch_list:
            if ev.get("kind") == "voice":
                day3_voice_dispatched.append({"wf_id": wid, **ev})
    for wid, kinds in sequencer["deferred_kinds"].items():
        if "voice" in kinds or "email-personal" in kinds:
            day3_voice_skipped_wf.add(wid)

    day3_fired = len(day3_voice_dispatched)
    day3_skipped = len(day3_voice_skipped_wf) - day3_fired  # deferred but not dispatched
    day3_skip_reason = "awaiting_sent_email_cold_1"

    # --- Day-5 personal sent ---
    day5_dispatches: list[dict] = []
    for wid, dispatch_list in sequencer["dispatched"].items():
        for ev in dispatch_list:
            if ev.get("kind") == "email-personal":
                # Enrich with lead info
                lead_info = day0_status.get(wid) or {}
                day5_dispatches.append({
                    "wf_id": wid,
                    "ob_id": (ev.get("outbound_job_ids") or [None])[0],
                    "sent_at": ev.get("sent_at") or ev.get("ts"),
                    "status": ev.get("status"),
                    "email": lead_info.get("email"),
                    "lead_title": lead_info.get("lead_title", wid),
                })
    # Enrich with vault lead name
    lead_map = {lead.get("workflow_id"): lead for lead in icp_leads}
    for d in day5_dispatches:
        vault_lead = lead_map.get(d["wf_id"])
        if vault_lead:
            d["lead_name"] = vault_lead.get("lead_name", "")
            d["company"] = vault_lead.get("company", "")
            d["email"] = d["email"] or vault_lead.get("lead_email", "")
            d["lead_title"] = vault_lead.get("lead_name", d["lead_title"])

    day5_count = len(day5_dispatches)

    # --- Opens (from Resend, for ICP leads whose Day-5 email was sent via Resend) ---
    icp_opens = sum(
        1 for e in resend_events.values()
        if (e.get("last_event") or "") in {"opened", "clicked"}
    )
    icp_open_rate = (icp_opens / max(icp_resend_total, 1)) * 100 if icp_resend_total > 0 else None

    # --- Per-contact Resend status for Day-5 dispatches ---
    day5_contact_status: list[dict] = []
    for d in day5_dispatches:
        email = (d.get("email") or "").lower()
        resend_info = resend_events.get(email)
        if resend_info:
            evt = resend_info.get("last_event", "pending")
            ago = resend_info.get("human_ago", "?")
            status_str = f"{evt} {ago}"
        else:
            # Not yet in Resend — still queued in sequencer
            ob_id = d.get("ob_id", "?")
            sent_at = d.get("sent_at", "")
            ago = _human_ago(sent_at) if sent_at else "?"
            status_str = f"dispatched {ago} — pending Resend confirm ({ob_id})"
        day5_contact_status.append({
            "name": d.get("lead_name") or d.get("lead_title", ""),
            "company": d.get("company", ""),
            "email": email,
            "ob_id": d.get("ob_id"),
            "status": status_str,
        })

    # --- Replies (from reply-router.jsonl, ICP-email-matched only) ---
    GENUINE_LABELS = frozenset({
        "sales_inquiry", "interested", "meeting_request", "positive",
        "question", "objection_with_counter",
    })
    rv = _real_vault()
    router_rows = _read_jsonl(rv / "operations" / "reply-router.jsonl")
    icp_email_set = {e.lower() for e in icp_emails}
    icp_genuine_replies = [
        r for r in router_rows
        if r.get("label") in GENUINE_LABELS
        and (r.get("from_email") or r.get("email") or "").lower() in icp_email_set
    ]
    # Also check the arjun reply (wf_050fb1d53cb7) which is a known ICP reply
    arjun_reply = [
        r for r in router_rows
        if r.get("label") in GENUINE_LABELS
        and "rtrvr" in (r.get("from_email") or r.get("email") or "").lower()
    ]
    icp_replies = len(icp_genuine_replies) + len(arjun_reply)

    # --- Demos / Closes (from DB deal_close) ---
    demos = 0
    closes = 0
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute(
                "SELECT stage, COUNT(*) FROM workflows WHERE kind='deal_close' "
                "GROUP BY stage"
            )
            for stage, cnt in cur.fetchall():
                if stage in ("qualified", "demo", "call_booked"):
                    demos += cnt
                elif stage in ("closed",):
                    closes += cnt
            conn.close()
        except sqlite3.Error:
            pass

    # --- Legacy summary (pre-partition) ---
    legacy_sends = _read_jsonl(rv / "operations" / "email-sends.jsonl")
    legacy_sent_count = sum(
        1 for e in legacy_sends
        if e.get("status") == "sent"
        and (e.get("ts") or "")[:10] < ICP_PARTITION_DATE
    )
    # Open/reply from Resend for legacy (use old global Resend sample)
    # These are surfaced in the existing funnel; just note the aggregate.
    legacy_resend_total = 0
    legacy_opens = 0
    legacy_api = _resend_get("/emails?limit=100")
    if legacy_api:
        for e in (legacy_api.get("data") or []):
            if (e.get("created_at") or "")[:10] < ICP_PARTITION_DATE:
                legacy_resend_total += 1
                if (e.get("last_event") or "") in {"opened", "clicked"}:
                    legacy_opens += 1
    legacy_open_rate = (legacy_opens / legacy_resend_total * 100) if legacy_resend_total > 0 else None

    return {
        "partition_date": ICP_PARTITION_DATE,
        "legacy": {
            "total_sent": legacy_sent_count,
            "resend_sample": legacy_resend_total,
            "opens": legacy_opens,
            "open_rate_pct": legacy_open_rate,
            "note": "Pre-ICP SMB spray — informational only, do not use for ICP signal",
        },
        "icp": {
            "total_icp_leads": len(icp_leads),
            "day0": {
                "tracked": day0_total_tracked,
                "sent": day0_sent_count,
                "bounced": day0_bounced,
                "resend_delivered": icp_resend_delivered,
                "resend_bounced": icp_resend_bounced,
                "resend_total": icp_resend_total,
                "delivery_rate_pct": delivery_rate,
            },
            "day3_voice": {
                "fired": day3_fired,
                "skipped": max(0, day3_skipped),
                "skip_reason": day3_skip_reason,
            },
            "day5_personal": {
                "sent": day5_count,
                "contacts": day5_contact_status,
            },
            "opens": {
                "count": icp_opens,
                "total_sent_to_resend": icp_resend_total,
                "rate_pct": icp_open_rate,
            },
            "replies": {
                "genuine": icp_replies,
                "rate_pct": (
                    icp_replies / max(day5_count + day0_sent_count, 1) * 100
                    if (day5_count + day0_sent_count) > 0 else None
                ),
            },
            "demos": demos,
            "closes": closes,
        },
    }


def render_icp_partition(partition: dict, no_colour: bool = False) -> str:
    """Render the partitioned funnel view with colour-coded benchmarks."""
    lines: list[str] = []
    pdate = partition["partition_date"]
    legacy = partition["legacy"]
    icp = partition["icp"]

    G = "\033[32m" if not no_colour else ""
    Y = "\033[33m" if not no_colour else ""
    R = "\033[31m" if not no_colour else ""
    B = "\033[1m" if not no_colour else ""
    DIM = "\033[2m" if not no_colour else ""
    RST = "\033[0m" if not no_colour else ""

    lines.append("")
    lines.append("╔══════════════════════════════════════════════════════════════════╗")
    lines.append(f"║  📊 FUNNEL PARTITION  (cutoff: {pdate})                   ║")
    lines.append("╚══════════════════════════════════════════════════════════════════╝")

    # ── Legacy block ──────────────────────────────────────────
    lines.append(f"")
    lines.append(f"{DIM}━━ Pre-{pdate} (legacy SMB spray) — informational only ━━{RST}")
    leg_sent = legacy["total_sent"]
    leg_sample = legacy["resend_sample"]
    leg_open_rate = legacy["open_rate_pct"]
    leg_open_str = f"{leg_open_rate:.0f}%" if leg_open_rate is not None else "n/a"
    lines.append(f"  Sent:        {leg_sent:>6}  (all-time SMB spray before ICP fix)")
    lines.append(f"  Resend sample: {leg_sample:>4}  opens: {leg_open_str}  {DIM}(27% open / 0.33% reply historical){RST}")
    lines.append(f"  {DIM}⚠  This data conflates role-accounts, non-ICP, and bulk spray.{RST}")
    lines.append(f"  {DIM}   Do NOT use for ICP conversion benchmarking.{RST}")

    # ── ICP block ─────────────────────────────────────────────
    lines.append("")
    lines.append(f"{B}━━ Post-{pdate} (ICP multi-touch) ━━ THE SIGNAL ━━━━━━━━━━━{RST}")
    lines.append(f"  ICP leads in vault:  {icp['total_icp_leads']}")
    lines.append("")

    # Day-0
    d0 = icp["day0"]
    d0_tracked = d0["tracked"]
    d0_sent = d0["sent"]
    d0_resend_del = d0["resend_delivered"]
    d0_resend_total = d0["resend_total"]
    d0_dr = d0["delivery_rate_pct"]
    if d0_dr is None:
        if d0_resend_total == 0 and d0_sent == 0:
            d0_dr_str = f"{DIM}no Resend confirms yet{RST}"
        else:
            d0_dr_str = "n/a"
    else:
        dr_val = d0_dr
        dr_label = f"{dr_val:.0f}%"
        if dr_val >= 95:
            d0_dr_str = f"{G}{dr_label} ✓{RST}"
        elif dr_val >= 80:
            d0_dr_str = f"{Y}{dr_label}{RST}"
        else:
            d0_dr_str = f"{R}{dr_label} ✗{RST}"
    lines.append(f"  Day-0 cold sent:    {d0_sent}/{d0_tracked} tracked | Resend: {d0_resend_del}/{d0_resend_total} delivered | rate: {d0_dr_str}")

    # Day-3 voice
    d3 = icp["day3_voice"]
    d3_fired = d3["fired"]
    d3_skip = d3["skipped"]
    d3_reason = d3["skip_reason"]
    if d3_fired == 0:
        voice_str = f"{Y}0 fired{RST}  {DIM}({d3_skip} skipped — reason: {d3_reason}){RST}"
    else:
        voice_str = f"{G}{d3_fired} fired{RST}  ({d3_skip} skipped)"
    lines.append(f"  Day-3 voice:        {voice_str}")

    # Day-5 personal
    d5 = icp["day5_personal"]
    d5_count = d5["sent"]
    d5_str = f"{G}{d5_count} dispatched{RST}" if d5_count > 0 else f"{R}0{RST}"
    lines.append(f"  Day-5 personal:     {d5_str}  {DIM}(today's new metric){RST}")
    for cs in d5["contacts"]:
        name = cs.get("name") or cs.get("email") or "?"
        company = cs.get("company", "")
        status = cs.get("status", "?")
        ob = cs.get("ob_id") or ""
        # Colour by keyword in status
        if "opened" in status or "clicked" in status:
            s_str = f"{G}{status}{RST}"
        elif "pending" in status or "queued" in status or "dispatched" in status:
            s_str = f"{Y}{status}{RST}"
        elif "bounced" in status or "failed" in status:
            s_str = f"{R}{status}{RST}"
        else:
            s_str = status
        lines.append(f"    • {name} ({company}): {s_str}")

    # Opens
    op = icp["opens"]
    op_count = op["count"]
    op_total = op["total_sent_to_resend"]
    op_rate = op["rate_pct"]
    if op_rate is None:
        op_str = f"{DIM}0/{op_total} — no opens recorded{RST}"
    else:
        op_label = f"{op_count}/{max(op_total, 1)} = {op_rate:.0f}%"
        if op_rate >= 25:
            op_str = f"{G}{op_label} ✓ (>25% target){RST}"
        elif op_rate >= 10:
            op_str = f"{Y}{op_label}{RST}"
        else:
            op_str = f"{R}{op_label} ✗ (<25% target){RST}"
    lines.append(f"  Opens (ICP Resend): {op_str}")

    # Replies
    rp = icp["replies"]
    rp_count = rp["genuine"]
    rp_rate = rp["rate_pct"]
    if rp_rate is None:
        rp_str = f"{rp_count} genuine  {DIM}(rate n/a — no denominator){RST}"
    else:
        rp_label = f"{rp_count} genuine = {rp_rate:.1f}%"
        if rp_rate >= 2:
            rp_str = f"{G}{rp_label} ✓ (>2% target){RST}"
        elif rp_rate >= 0.5:
            rp_str = f"{Y}{rp_label}{RST}"
        else:
            rp_str = f"{R}{rp_label} ✗ (<2% target){RST}"
    lines.append(f"  Replies (ICP):      {rp_str}")

    # Demos
    demo_count = icp["demos"]
    demo_rate = (demo_count / max(rp_count, 1) * 100) if rp_count > 0 else None
    if demo_rate is None:
        demo_str = f"{demo_count}  {DIM}(rate n/a — no replies yet){RST}"
    else:
        demo_label = f"{demo_count} = {demo_rate:.0f}%"
        if demo_rate >= 20:
            demo_str = f"{G}{demo_label} ✓ (>20% target){RST}"
        elif demo_rate >= 10:
            demo_str = f"{Y}{demo_label}{RST}"
        else:
            demo_str = f"{R}{demo_label} ✗ (<20% target){RST}"
    lines.append(f"  Demos / calls:      {demo_str}")

    # Closes
    close_count = icp["closes"]
    lines.append(f"  Closes (Managed):   {close_count}")

    lines.append("")
    lines.append(f"  Benchmarks: Day-0 delivery {G}>95%{RST} | Open {G}>25%{RST} | Reply {G}>2%{RST} | Demo {G}>20%{RST}")

    return "\n".join(lines)


def render_strategy_targets(partition: dict, no_colour: bool = False) -> str:
    """Render Strategy B targets vs current trajectory."""
    lines: list[str] = []
    G = "\033[32m" if not no_colour else ""
    Y = "\033[33m" if not no_colour else ""
    R = "\033[31m" if not no_colour else ""
    B = "\033[1m" if not no_colour else ""
    DIM = "\033[2m" if not no_colour else ""
    RST = "\033[0m" if not no_colour else ""

    icp = partition["icp"]
    launches_ago_days = (
        _now_utc().date() -
        datetime.fromisoformat(STRATEGY_B_LAUNCH_DATE).date()
    ).days
    months_elapsed = launches_ago_days / 30.4

    # Strategy B month-3 target = 1 Managed close / month
    target_closes_by_now = (
        STRATEGY_B_MANAGED_CLOSE_TARGET
        if months_elapsed >= STRATEGY_B_MANAGED_CLOSE_PER_MONTH_FROM
        else 0
    )
    actual_closes = icp["closes"]
    gap_closes = target_closes_by_now - actual_closes
    monthly_mrr_target = STRATEGY_B_MANAGED_CLOSE_TARGET * STRATEGY_B_MANAGED_MRR

    lines.append("")
    lines.append(f"{B}━━ Strategy B: targets vs reality ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}")
    lines.append(f"  ICP sequence launched:    {STRATEGY_B_LAUNCH_DATE}  ({launches_ago_days}d ago, {months_elapsed:.1f} months)")
    lines.append(f"  Month 3+ target:          {STRATEGY_B_MANAGED_CLOSE_TARGET} Managed close/month  (${monthly_mrr_target}/mo MRR)")
    lines.append("")

    # Pipeline health
    d5_sent = icp["day5_personal"]["sent"]
    replies = icp["replies"]["genuine"]
    demos = icp["demos"]
    closes = icp["closes"]
    lines.append(f"  Current pipeline:")
    lines.append(f"    ICP leads tracked:       {icp['total_icp_leads']}")
    lines.append(f"    Day-5 personal fired:    {d5_sent}  {DIM}(today){RST}")
    lines.append(f"    Genuine replies:         {replies}")
    lines.append(f"    Demos booked:            {demos}")
    lines.append(f"    Managed closes:          {closes}")
    lines.append("")

    # Gap
    if months_elapsed < STRATEGY_B_MANAGED_CLOSE_PER_MONTH_FROM:
        months_to_target = STRATEGY_B_MANAGED_CLOSE_PER_MONTH_FROM - months_elapsed
        lines.append(
            f"  {Y}⏳ Still {months_to_target:.1f} months before month-3 close target activates.{RST}"
        )
        lines.append(
            f"     Need to be at demo stage by month 3 to hit the target on time."
        )
    elif gap_closes > 0:
        lines.append(
            f"  {R}⚠ BEHIND TARGET: {gap_closes} close(s) behind Strategy B month-3 goal.{RST}"
        )
        # What needs to happen
        replies_needed_for_demo = max(0, 1 - demos)
        lines.append(
            f"  Next unlock: {replies_needed_for_demo} demo booked from current {replies} genuine replies."
        )
    else:
        lines.append(
            f"  {G}✓ On track (or ahead of) Strategy B month-3 close target.{RST}"
        )

    # Biggest gap to Strategy B
    if d5_sent == 0:
        biggest_gap = "Day-5 personal: 0 sent — Day-0 delivery unconfirmed blocks sequencer"
    elif replies == 0:
        biggest_gap = f"Reply rate: 0 genuine replies from {d5_sent} Day-5 personal touches — need opens + responses"
    elif demos == 0:
        biggest_gap = f"Demo conversion: {replies} genuine replies, 0 demos booked — need to close the loop"
    else:
        biggest_gap = f"Close rate: {demos} demos, 0 closes — working the pipeline"

    lines.append("")
    lines.append(f"  {B}Biggest gap to Strategy B target:{RST}")
    lines.append(f"  → {biggest_gap}")
    lines.append("")
    lines.append("─" * 70)

    return "\n".join(lines)


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
    ap.add_argument("--no-partition", action="store_true",
                    help="Skip ICP partition block (legacy output only)")
    ap.add_argument("--partition-only", action="store_true",
                    help="Print ICP partition + strategy targets, skip full funnel")
    args = ap.parse_args()

    no_colour = args.no_colour or not sys.stdout.isatty()

    data = build_funnel(days=args.days)

    # Build partition data (additive — does not affect existing funnel data)
    partition: Optional[dict] = None
    if not args.no_partition and not args.compact:
        try:
            partition = build_icp_partition()
            data["icp_partition"] = partition  # embed in JSON output
        except Exception as exc:
            sys.stderr.write(f"[funnel-pulse] partition build failed: {exc}\n")

    if args.json_output:
        print(json.dumps(data, indent=2, default=str))
    elif args.compact:
        print(render_compact(data))
    elif args.partition_only:
        if partition:
            print(render_icp_partition(partition, no_colour=no_colour))
            print(render_strategy_targets(partition, no_colour=no_colour))
        else:
            print("[funnel-pulse] partition data unavailable")
    else:
        # Default: partition block first, then full funnel
        if partition:
            print(render_icp_partition(partition, no_colour=no_colour))
            print(render_strategy_targets(partition, no_colour=no_colour))
        print(render_funnel(data, no_colour=no_colour))

    if not args.no_write:
        write_snapshot(data)


if __name__ == "__main__":
    main()
