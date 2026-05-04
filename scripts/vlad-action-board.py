#!/usr/bin/env python3
"""vlad-action-board.py — Single-glance board of everything Vlad needs to do RIGHT NOW.

Read-only. Never sends, fires, or mutates anything.
Outputs a clean ASCII checklist with priority, time-estimate, and blocker impact.

Priority levels:
  P0 — Revenue or sender-reputation at risk; do today
  P1 — Significant ROI or unblocks autonomous work; do this week
  P2 — Good-to-have; delegate or batch

OVERDUE: item has been waiting >24h without human action.

CLI:
  python3 scripts/vlad-action-board.py
  python3 scripts/vlad-action-board.py --json        # machine-readable dict
  python3 scripts/vlad-action-board.py --brief        # P0s only (for digest embedding)
  python3 scripts/vlad-action-board.py --mark-flag    # write RICK_VLAD_ACTIONS_LIVE sentinel

De-duplication: items already surfaced as automated metrics in rick-activity-digest.py
(flag-health, funnel counts, bounce rates) are NOT listed here — only Vlad-action items.

────────────────────────────────────────────────────────────────────────
OPENCLAW COMMITMENTS MIGRATION (2026-05-04 / OpenClaw 2026.5.3)
────────────────────────────────────────────────────────────────────────
The APPROVALS NEEDED section now tries `openclaw commitments list --status=pending
--json` as a primary source before falling back to the legacy file-walk.

Known gaps in openclaw commitments 2026.5.3:
  GAP-1: No --kind flag → cannot do --kind=approval from CLI.
          Workaround: fetch all pending commitments, filter on kind/text in Python.
  GAP-2: No create/update subcommands → commitments are inferred from conversation
          context only; workflow artifacts in mailbox/drafts/auto/ are NOT auto-ingested.
  GAP-3: commitments.enabled is off by default → store will be empty until enabled
          via `openclaw config set commitments.enabled true`.
  GAP-4: openclaw commitments list itself does NOT invoke an LLM (read-only local
          store). The background extraction pass DOES use a model, but the model
          is not configurable from this CLI.
  GAP-5: Commitments scope = conversation channel; mailbox/drafts/auto/ items are
          not automatically surfaced as commitments.

Fallback policy: if commitments returns 0 approval-like items (or errors),
_gather_auto_drafts() falls back to _gather_auto_drafts_legacy() (the original
file-walk). All other APPROVALS sub-functions are unchanged.

Callers of this script are unaffected — CLI interface is identical.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS = DATA_ROOT / "operations"

DRAFTS_AUTO_DIR    = DATA_ROOT / "mailbox" / "drafts" / "auto"
DRAFTS_DIR         = DATA_ROOT / "mailbox" / "drafts"
TRIAGE_DIR         = DATA_ROOT / "mailbox" / "triage"
APPROVALS_MD       = DATA_ROOT / "control" / "approvals.md"
SUPPRESSION_VIOLS  = OPS / "suppression-violations.jsonl"
FALLBACK_LOG       = OPS / "llm-fallback-events.jsonl"
MEMELORD_LOG_PATH  = OPS / "memelord-pipeline.jsonl"
SENDER_WARMUP_FILE = DATA_ROOT / "control" / "sender-warmup-state.json"
ANTHROPIC_CREDITS  = DATA_ROOT / "control" / "anthropic-credits.json"
MOLTBOOK_CREDS     = Path.home() / ".config" / "moltbook" / "credentials.json"
FENIX_DECISIONS    = OPS / "fenix-decisions.jsonl"
EXPERIMENT_QUEUE   = DATA_ROOT / "control" / "experiment-queue.json"
BOARD_LOG          = OPS / "vlad-action-board.jsonl"

OVERDUE_HOURS = 24  # hours before an item is marked OVERDUE


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        s = raw.replace("Z", "+00:00") if isinstance(raw, str) else raw
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _age_hours(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    return (_now() - ts).total_seconds() / 3600.0


def _is_overdue(ts: Optional[datetime]) -> bool:
    age = _age_hours(ts)
    return age is not None and age >= OVERDUE_HOURS


def _iter_jsonl_reverse(path: Path, max_lines: int = 8000):
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in reversed(lines[-max_lines:]):
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _db_connect() -> Optional[sqlite3.Connection]:
    """Return a sqlite3 connection to the runtime DB, or None on failure."""
    import subprocess
    result = subprocess.run(
        ["bash", "-c",
         "source /Users/rickthebot/.openclaw/workspace/config/rick.env 2>/dev/null"
         " && echo $RICK_RUNTIME_DB_FILE"],
        capture_output=True, text=True, timeout=5,
    )
    db_path = result.stdout.strip() or str(
        Path.home() / "rick-vault" / "runtime" / "rick-runtime.db"
    )
    if not Path(db_path).exists():
        return None
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        return None


# ── Task item dataclass (lightweight) ───────────────────────────────────────

class Task:
    """A single actionable item Vlad must handle."""
    __slots__ = ("section", "priority", "label", "detail",
                 "est_mins", "blocked_if_not_done", "overdue",
                 "created_ts", "count")

    def __init__(
        self, *,
        section: str,
        priority: str,               # P0 / P1 / P2
        label: str,
        detail: str = "",
        est_mins: int = 5,
        blocked: str = "",
        overdue: bool = False,
        created_ts: Optional[datetime] = None,
        count: int = 1,
    ) -> None:
        self.section = section
        self.priority = priority
        self.label = label
        self.detail = detail
        self.est_mins = est_mins
        self.blocked_if_not_done = blocked
        self.overdue = overdue
        self.created_ts = created_ts
        self.count = count

    def age_str(self) -> str:
        if self.created_ts is None:
            return ""
        age = _age_hours(self.created_ts)
        if age is None:
            return ""
        if age < 1:
            return f"{int(age*60)}m ago"
        return f"{age:.0f}h ago"

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "priority": self.priority,
            "label": self.label,
            "detail": self.detail,
            "est_mins": self.est_mins,
            "blocked_if_not_done": self.blocked_if_not_done,
            "overdue": self.overdue,
            "created_ts": self.created_ts.isoformat() if self.created_ts else None,
            "count": self.count,
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — APPROVALS NEEDED
# ═══════════════════════════════════════════════════════════════════════════

# ── openclaw commitments integration ───────────────────────────────────────
# Migration: APPROVALS NEEDED now queries openclaw commitments list first.
# API (2026.5.3): list [--status=pending] [--agent <id>] [--json]
#                  dismiss <id...>
# Schema fields present in records (from store inspection + docs):
#   id, agentId, sessionKey, status, kind, text/check_in, due_at, created_at,
#   channel, scope
# GAP-1: no --kind CLI flag → we filter kind/text in Python (see below).
# GAP-4: list command is a local store read — no LLM invoked here.

_APPROVAL_TEXT_KEYWORDS = ("approve", "approval", "review", "send", "draft", "reply", "decision")


def _run_commitments_list(status: str = "pending") -> list[dict]:
    """Shell out to `openclaw commitments list --status=<status> --json`.

    Returns parsed commitment records, or [] on any error (graceful fallback).
    No LLM is invoked by this call — it is a local JSON store read.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["openclaw", "commitments", "list", f"--status={status}", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("commitments", [])
    except Exception:
        return []


def _gather_approvals_via_commitments() -> list[Task]:
    """PRIMARY path: query `openclaw commitments list --status=pending --json`.

    Maps commitment records to APPROVALS NEEDED Tasks.
    Filters for approval-like items because no --kind=approval flag exists (GAP-1).
    Returns [] when: store is empty, commitments disabled, no matching items, or error.
    Callers must fall back to _gather_auto_drafts_legacy() when this returns [].
    """
    records = _run_commitments_list(status="pending")
    tasks: list[Task] = []
    for rec in records:
        text = str(rec.get("text") or rec.get("check_in") or "").lower()
        kind = str(rec.get("kind") or "").lower()
        # Accept if kind field contains approval semantics OR text matches keywords
        # (GAP-1 workaround — no --kind=approval flag in this CLI version)
        is_approval_like = (
            "approval" in kind
            or "draft" in kind
            or any(kw in text for kw in _APPROVAL_TEXT_KEYWORDS)
        )
        if not is_approval_like:
            continue
        cm_id = rec.get("id", "?")
        due = _parse_ts(rec.get("due_at") or rec.get("created_at"))
        display_text = (rec.get("text") or rec.get("check_in") or cm_id)[:80]
        tasks.append(Task(
            section="APPROVALS NEEDED",
            priority="P0",
            label=f"Commitment: {display_text}",
            detail=(
                f"id={cm_id}  |  kind={rec.get('kind', 'inferred')}  "
                f"|  Dismiss: openclaw commitments dismiss {cm_id}  "
                f"|  source=openclaw-commitments"
            ),
            est_mins=5,
            blocked="Open commitment loop; heartbeat will re-surface until dismissed",
            overdue=_is_overdue(due),
            created_ts=due,
        ))
    return tasks


def _gather_auto_drafts() -> list[Task]:
    """Auto-drafted replies in drafts/auto/ awaiting Vlad send-approval.

    Migration (2026-05-04): tries `openclaw commitments list --status=pending` first.
    Falls back to _gather_auto_drafts_legacy() (file-walk) when commitments returns
    0 approval-like items — which is expected while commitments.enabled is off/empty.

    GAP-2: Commitments are inferred from conversation; workflow file artifacts in
    mailbox/drafts/auto/ are not auto-ingested. The legacy file-walk remains the
    live implementation until OpenClaw infers these drafts from heartbeat context.
    GAP-3: Store empty until `openclaw config set commitments.enabled true`.
    """
    # 1. Primary: openclaw commitments path
    commitments_tasks = _gather_approvals_via_commitments()
    if commitments_tasks:
        return commitments_tasks
    # 2. Fallback: legacy file-walk (active implementation)
    return _gather_auto_drafts_legacy()


def _gather_auto_drafts_legacy() -> list[Task]:
    """LEGACY fallback: file-walk over mailbox/drafts/auto/ for pending send-approvals.

    This is the active implementation while openclaw commitments store is empty.
    Preserved verbatim — do not modify without updating _gather_auto_drafts() too.
    """
    tasks: list[Task] = []
    if not DRAFTS_AUTO_DIR.is_dir():
        return tasks

    files = sorted(DRAFTS_AUTO_DIR.glob("*.json"))
    if not files:
        return tasks

    overdue_count = 0
    oldest_ts: Optional[datetime] = None

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        created = _parse_ts(data.get("created_at") or data.get("ran_at"))
        if created and (oldest_ts is None or created < oldest_ts):
            oldest_ts = created
        if _is_overdue(created):
            overdue_count += 1

    is_overdue = overdue_count > 0

    # Summarise by from_email for detail
    froms = []
    for f in files[:5]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Various field names used across draft schemas
            from_str = (
                data.get("from_email")
                or data.get("prospect_email")
                or data.get("from")
                or data.get("email")
                or "?"
            )
            froms.append(from_str)
        except Exception:
            froms.append("?")
    detail_str = ", ".join(froms) + ("…" if len(files) > 5 else "")
    first_wf_id = files[0].stem
    preferred_file = None
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        candidate = str(data.get("wf_id") or f.stem)
        if candidate.startswith("wf_"):
            preferred_file = f
            first_wf_id = candidate
            break
    if preferred_file is None:
        try:
            first_data = json.loads(files[0].read_text(encoding="utf-8"))
            first_wf_id = str(first_data.get("wf_id") or first_wf_id)
        except Exception:
            pass

    tasks.append(Task(
        section="APPROVALS NEEDED",
        priority="P0",
        label=f"Send or discard {len(files)} auto-drafted reply/replies",
        detail=f"From: {detail_str}  |  Path: mailbox/drafts/auto/  |  Run: python3 scripts/send-draft.py {first_wf_id}",
        est_mins=3 * len(files),
        blocked="Hot sales leads go cold — Rick can't advance these threads without approval",
        overdue=is_overdue,
        created_ts=oldest_ts,
        count=len(files),
    ))
    return tasks


def _gather_open_approvals_md() -> list[Task]:
    """Parse approvals.md for open (non-approved, non-rejected) launch approvals."""
    tasks: list[Task] = []
    if not APPROVALS_MD.exists():
        return tasks

    open_items: list[dict] = []
    try:
        text = APPROVALS_MD.read_text(encoding="utf-8")
    except OSError:
        return tasks

    # Parse markdown table rows: | Date | Status | Owner | Area | Request | Impact |
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if len(cols) < 4:
            continue
        date_str, status, owner, area = cols[0], cols[1], cols[2], cols[3]
        request = cols[4] if len(cols) > 4 else ""
        if status.lower() in ("status", "------", ""):
            continue  # header / separator
        if status.lower() not in ("open", "pending"):
            continue
        # Extract approval ID from request
        apr_id = ""
        for part in request.split():
            if part.startswith("[apr_") and part.endswith("]"):
                apr_id = part[1:-1]
                break
        ts = _parse_ts(date_str)
        open_items.append({
            "date": date_str, "area": area, "request": request[:80],
            "apr_id": apr_id, "ts": ts,
        })

    if not open_items:
        return tasks

    overdue_count = sum(1 for i in open_items if _is_overdue(i["ts"]))
    oldest_ts = min((i["ts"] for i in open_items if i["ts"]), default=None)
    oldest_age = _age_hours(oldest_ts)
    oldest_days = f"{oldest_age/24:.0f}d" if oldest_age else "?"

    ids = [i["apr_id"] for i in open_items if i["apr_id"]]
    tasks.append(Task(
        section="APPROVALS NEEDED",
        priority="P0",
        label=f"{len(open_items)} open launch approval(s) — oldest {oldest_days} old",
        detail="IDs: " + ", ".join(ids[:5]) + ("…" if len(ids) > 5 else "")
               + "  |  control/approvals.md",
        est_mins=5 * len(open_items),
        blocked="Launch packages for Working Title, PH Monitor etc. cannot publish until approved",
        overdue=overdue_count > 0,
        created_ts=oldest_ts,
        count=len(open_items),
    ))
    return tasks


def _gather_fenix_escalated() -> list[Task]:
    """Fenix-escalated artifacts requiring founder judgment."""
    tasks: list[Task] = []
    cutoff = _now() - timedelta(hours=48)  # look back 48h for escalations
    escalated: list[dict] = []

    for entry in _iter_jsonl_reverse(FENIX_DECISIONS):
        if entry.get("action") != "escalate":
            continue
        ts = _parse_ts(entry.get("ts", ""))
        if ts and ts < cutoff:
            break
        if ts:
            escalated.append({"ts": ts, "artifact_id": entry.get("artifact_id", "?"),
                               "reason": entry.get("reason", "")})

    if not escalated:
        return tasks

    oldest_ts = min(e["ts"] for e in escalated)
    tasks.append(Task(
        section="APPROVALS NEEDED",
        priority="P0",
        label=f"{len(escalated)} Fenix-escalated artifact(s) need founder call",
        detail=", ".join(e["artifact_id"] for e in escalated[:4]),
        est_mins=5 * len(escalated),
        blocked="Fenix holds artifact from shipping until Vlad proceeds/blocks",
        overdue=_is_overdue(oldest_ts),
        created_ts=oldest_ts,
        count=len(escalated),
    ))
    return tasks


def _gather_high_spend_approvals() -> list[Task]:
    """High-spend workflow approvals from runtime DB (status=pending_approval + cost > threshold)."""
    tasks: list[Task] = []
    con = _db_connect()
    if con is None:
        return tasks
    try:
        rows = con.execute(
            "SELECT w.id, w.kind, w.status, w.created_at, "
            "  ROUND(COALESCE((SELECT SUM(cost_usd) FROM outcomes WHERE workflow_id=w.id),0),3) AS cost "
            "FROM workflows w "
            "WHERE w.status IN ('pending_approval','pending') "
            "ORDER BY cost DESC LIMIT 10"
        ).fetchall()
        high = [dict(r) for r in rows if r["cost"] > 1.0]
        if high:
            oldest_ts = _parse_ts(min(r["created_at"] for r in high))
            total_cost = sum(r["cost"] for r in high)
            tasks.append(Task(
                section="APPROVALS NEEDED",
                priority="P1",
                label=f"{len(high)} high-cost workflow(s) pending approval (${total_cost:.2f} at stake)",
                detail=", ".join(f"{r['id'][:12]} [{r['kind']}] ${r['cost']}" for r in high[:3]),
                est_mins=5,
                blocked="Workflows paused; spend counter ticking",
                overdue=_is_overdue(oldest_ts),
                created_ts=oldest_ts,
                count=len(high),
            ))
    except Exception:
        pass
    finally:
        con.close()
    return tasks


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — INBOX
# ═══════════════════════════════════════════════════════════════════════════

def _gather_moltbook_dms() -> list[Task]:
    """Moltbook unread DM count via API."""
    tasks: list[Task] = []
    if not MOLTBOOK_CREDS.exists():
        return tasks
    try:
        creds = json.loads(MOLTBOOK_CREDS.read_text(encoding="utf-8"))
        api_key = creds.get("api_key", "")
        if not api_key:
            return tasks
        req = urllib.request.Request(
            "https://www.moltbook.com/api/v1/notifications/unread-count",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp = urllib.request.urlopen(req, timeout=6)
        data = json.loads(resp.read().decode())
        unread = int(data.get("unread_count", 0))
    except Exception:
        return tasks

    if unread == 0:
        return tasks

    priority = "P0" if unread >= 50 else "P1" if unread >= 10 else "P2"
    tasks.append(Task(
        section="INBOX",
        priority=priority,
        label=f"{unread} unread Moltbook DM(s)",
        detail="moltbook.com/messages — may include warm leads or agent-to-agent pings",
        est_mins=max(5, unread // 10),
        blocked="Warm leads or referrals going cold in DMs",
        overdue=False,
        count=unread,
    ))
    return tasks


def _gather_resend_inbox() -> list[Task]:
    """Unread/unrouted emails in Resend triage JSONL files."""
    tasks: list[Task] = []
    if not TRIAGE_DIR.is_dir():
        return tasks

    total_unread = 0
    oldest_ts: Optional[datetime] = None

    today = _now().strftime("%Y-%m-%d")
    yesterday = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")

    for date_str in (today, yesterday):
        tfile = TRIAGE_DIR / f"inbound-{date_str}.jsonl"
        if not tfile.exists():
            continue
        try:
            for line in tfile.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("router_ran_at"):
                    continue  # already processed
                total_unread += 1
                ts = _parse_ts(row.get("classified_at") or row.get("received_at"))
                if ts and (oldest_ts is None or ts < oldest_ts):
                    oldest_ts = ts
        except OSError:
            pass

    if total_unread == 0:
        return tasks

    tasks.append(Task(
        section="INBOX",
        priority="P1",
        label=f"{total_unread} unread/unrouted email(s) in Resend triage",
        detail="mailbox/triage/inbound-*.jsonl — router has not yet dispatched these",
        est_mins=2 * total_unread,
        blocked="Warm leads may not get counter-pitch or sales reply",
        overdue=_is_overdue(oldest_ts),
        created_ts=oldest_ts,
        count=total_unread,
    ))
    return tasks


def _gather_telegram_ops() -> list[Task]:
    """Check Telegram ops-alerts topic for backlogged messages (last_seen_at > 4h ago)."""
    tasks: list[Task] = []
    con = _db_connect()
    if con is None:
        return tasks
    try:
        row = con.execute(
            "SELECT topic_key, title, last_seen_at FROM telegram_topics "
            "WHERE topic_key='ops-alerts' OR slug='ops-alerts' "
            "ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        if row:
            last_seen = _parse_ts(row["last_seen_at"])
            age = _age_hours(last_seen)
            if age and age > 4:
                tasks.append(Task(
                    section="INBOX",
                    priority="P2",
                    label=f"Telegram ops-alerts unchecked for {age:.0f}h",
                    detail="Telegram → Ops Alerts topic — Rick may have posted alerts",
                    est_mins=3,
                    blocked="Silent incidents, stale alerts, or escalations missed",
                    overdue=age >= OVERDUE_HOURS,
                    created_ts=last_seen,
                ))
    except Exception:
        pass
    finally:
        con.close()
    return tasks


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — MANUAL FIXES
# ═══════════════════════════════════════════════════════════════════════════

def _gather_anthropic_billing() -> list[Task]:
    """Alert if recent LLM billing-skip events suggest Anthropic credits are exhausted."""
    tasks: list[Task] = []
    cutoff = _now() - timedelta(hours=24)
    skip_events: list[datetime] = []

    for entry in _iter_jsonl_reverse(FALLBACK_LOG, max_lines=2000):
        ts = _parse_ts(entry.get("ts", ""))
        if ts is None:
            continue
        if ts < cutoff:
            break
        if entry.get("n_failed", 0) >= 4 and "anthropic" in str(entry.get("primary", "")):
            skip_events.append(ts)

    if not skip_events:
        return tasks

    # Check declared balance (manual entry — may be stale)
    balance_note = ""
    if ANTHROPIC_CREDITS.exists():
        try:
            c = json.loads(ANTHROPIC_CREDITS.read_text(encoding="utf-8"))
            balance_usd = c.get("balance_usd")
            updated = c.get("updated", "")
            balance_note = f"Last logged balance: ${balance_usd} (updated {updated})"
        except Exception:
            pass

    tasks.append(Task(
        section="MANUAL FIXES",
        priority="P0",
        label=f"Anthropic billing failure — {len(skip_events)} full-chain skip(s) in last 24h",
        detail=f"claude-opus-4-7 failing. {balance_note}  |  console.anthropic.com/settings/billing",
        est_mins=5,
        blocked="All review-route LLM jobs fail silently (auto-drafts, reply router, Fenix)",
        overdue=False,
        created_ts=min(skip_events),
        count=len(skip_events),
    ))
    return tasks


def _gather_memelord_credits() -> list[Task]:
    """Alert if Memelord credits are below 50 (check pipeline log)."""
    tasks: list[Task] = []
    latest_credits: Optional[int] = None
    latest_ts: Optional[datetime] = None

    for entry in _iter_jsonl_reverse(MEMELORD_LOG_PATH):
        c = entry.get("credits_remaining")
        if c is not None:
            latest_credits = int(c)
            latest_ts = _parse_ts(entry.get("ts", ""))
            break

    # Fall back to MEMORY constant (168 at launch, checked 2026-04-*)
    if latest_credits is None:
        # No log data; skip the alert — don't guess
        return tasks

    if latest_credits >= 50:
        return tasks

    priority = "P0" if latest_credits < 10 else "P1"
    tasks.append(Task(
        section="MANUAL FIXES",
        priority=priority,
        label=f"Memelord credits low: {latest_credits} remaining",
        detail="memelord.so — purchase credits before meme pipeline stalls",
        est_mins=5,
        blocked="Meme content pipeline will stall (videos=5cr, images=1cr each)",
        overdue=False,
        created_ts=latest_ts,
    ))
    return tasks


def _gather_suppression_violations() -> list[Task]:
    """Suppression violations requiring Vlad review (non-smoke-test entries)."""
    tasks: list[Task] = []
    if not SUPPRESSION_VIOLS.exists():
        return tasks

    cutoff = _now() - timedelta(hours=24)
    violations: list[dict] = []

    for entry in _iter_jsonl_reverse(SUPPRESSION_VIOLS):
        ts = _parse_ts(entry.get("ts", ""))
        if ts and ts < cutoff:
            break
        # Skip dry-run smoke tests — these are informational, not real violations
        subject = str(entry.get("subject", "")).lower()
        if "smoke test" in subject or "dry-run" in subject or "dry_run" in subject:
            continue
        violations.append({
            "to": entry.get("to", "?"),
            "reason": entry.get("suppression_reason", "?"),
            "violation": entry.get("violation", "?"),
            "ts": ts,
        })

    if not violations:
        return tasks

    oldest_ts = min((v["ts"] for v in violations if v["ts"]), default=None)
    to_list = ", ".join(v["to"] for v in violations[:4])
    tasks.append(Task(
        section="MANUAL FIXES",
        priority="P1",
        label=f"{len(violations)} real suppression-violation(s) in last 24h",
        detail=f"To: {to_list}  |  operations/suppression-violations.jsonl",
        est_mins=10,
        blocked="Repeated violations risk Resend account suspension or domain blacklist",
        overdue=_is_overdue(oldest_ts),
        created_ts=oldest_ts,
        count=len(violations),
    ))
    return tasks


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — STRATEGIC DECISIONS
# ═══════════════════════════════════════════════════════════════════════════

def _gather_icp_warmup() -> list[Task]:
    """Surface ICP cron resume decision after sender warmup completes."""
    tasks: list[Task] = []
    if not SENDER_WARMUP_FILE.exists():
        return tasks

    try:
        state = json.loads(SENDER_WARMUP_FILE.read_text(encoding="utf-8"))
    except Exception:
        return tasks

    started_raw = state.get("warmup_started_at", "")
    started_ts = _parse_ts(started_raw)
    if started_ts is None:
        return tasks

    day_num = max(1, int((_now() - started_ts).total_seconds() / 86400) + 1)
    # Standard warmup is 14 days; surface a heads-up at day 10+
    if day_num < 7:
        tasks.append(Task(
            section="STRATEGIC DECISIONS",
            priority="P2",
            label=f"Sender warmup in progress — Day {day_num}/14",
            detail=f"Started {started_ts.strftime('%Y-%m-%d')}. Full-volume ICP outreach unlocks Day 14.",
            est_mins=0,
            blocked="ICP cold outreach at reduced volume until warmup completes",
        ))
    elif day_num >= 14:
        tasks.append(Task(
            section="STRATEGIC DECISIONS",
            priority="P1",
            label=f"Sender warmup COMPLETE (Day {day_num}) — decide on full-volume ICP cron",
            detail="Flip RICK_ICP_OUTREACH_LIVE=1 in rick.env to resume full-volume campaigns",
            est_mins=5,
            blocked="ICP pipeline still throttled — revenue experiments starved of outreach volume",
            overdue=day_num > 16,
        ))
    else:
        # Days 7–13: approaching, flag as informational P2
        days_left = 14 - day_num
        tasks.append(Task(
            section="STRATEGIC DECISIONS",
            priority="P2",
            label=f"Sender warmup Day {day_num}/14 — {days_left}d until full-volume ICP unlocks",
            detail="Prepare ICP sequences now so they're ready to fire on Day 14",
            est_mins=0,
            blocked=None,
        ))
    return tasks


def _gather_experiment_decisions() -> list[Task]:
    """Surface queued experiments that are waiting >7 days without activation."""
    tasks: list[Task] = []
    if not EXPERIMENT_QUEUE.exists():
        return tasks

    try:
        q = json.loads(EXPERIMENT_QUEUE.read_text(encoding="utf-8"))
    except Exception:
        return tasks

    # Support both list and {experiments: [...], ...} shapes
    if isinstance(q, list):
        experiments = q
    elif isinstance(q, dict):
        experiments = q.get("experiments", [])
    else:
        return tasks
    active = [e for e in experiments if e.get("status") == "active"]
    queued = [e for e in experiments if e.get("status") not in ("active", "failed", "won", "killed")]

    if queued:
        oldest_queued = None
        for exp in queued:
            ts = _parse_ts(exp.get("created_at", "") or exp.get("queued_at", ""))
            if ts and (oldest_queued is None or ts < oldest_queued):
                oldest_queued = ts

        age = _age_hours(oldest_queued)
        # Only surface if >7 days and not enough active experiments
        if age and age > 168 and len(active) < 3:
            top_names = [e.get("title", e.get("name", "?"))[:40] for e in queued[:3]]
            tasks.append(Task(
                section="STRATEGIC DECISIONS",
                priority="P1",
                label=f"{len(queued)} queued experiment(s) idle >7d with only {len(active)} active",
                detail="Top queued: " + "; ".join(top_names),
                est_mins=10,
                blocked="Revenue experiments stagnating — MRR flat days accumulate",
                overdue=age > 336,
                created_ts=oldest_queued,
                count=len(queued),
            ))
    return tasks


def _gather_quickstart_cta() -> list[Task]:
    """Surface any quickstart-CTA evaluations from operations logs."""
    tasks: list[Task] = []
    # Check for any quickstart evaluation JSONL
    for cand in (
        OPS / "quickstart-cta-eval.jsonl",
        OPS / "post-install-nudges.jsonl",
    ):
        if not cand.exists():
            continue
        cutoff = _now() - timedelta(hours=48)
        pending: list[dict] = []
        for entry in _iter_jsonl_reverse(cand):
            ts = _parse_ts(entry.get("ts", ""))
            if ts and ts < cutoff:
                break
            if entry.get("status") in ("pending_review", "needs_approval"):
                pending.append(entry)
        if pending:
            oldest_ts = _parse_ts(pending[-1].get("ts", ""))
            tasks.append(Task(
                section="STRATEGIC DECISIONS",
                priority="P2",
                label=f"{len(pending)} quickstart-CTA evaluation(s) pending review",
                detail=cand.name,
                est_mins=5,
                blocked="Install-to-upgrade funnel degraded if CTA copy not approved",
                overdue=_is_overdue(oldest_ts),
                created_ts=oldest_ts,
                count=len(pending),
            ))
    return tasks


# ═══════════════════════════════════════════════════════════════════════════
# GATHER all tasks
# ═══════════════════════════════════════════════════════════════════════════

SECTION_ORDER = ["APPROVALS NEEDED", "INBOX", "MANUAL FIXES", "STRATEGIC DECISIONS"]


def gather_all() -> list[Task]:
    tasks: list[Task] = []
    # Approvals
    tasks += _gather_auto_drafts()
    tasks += _gather_open_approvals_md()
    tasks += _gather_fenix_escalated()
    tasks += _gather_high_spend_approvals()
    # Inbox
    tasks += _gather_moltbook_dms()
    tasks += _gather_resend_inbox()
    tasks += _gather_telegram_ops()
    # Manual fixes
    tasks += _gather_anthropic_billing()
    tasks += _gather_memelord_credits()
    tasks += _gather_suppression_violations()
    # Strategic
    tasks += _gather_icp_warmup()
    tasks += _gather_experiment_decisions()
    tasks += _gather_quickstart_cta()
    return tasks


# ═══════════════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════════════

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
PRIORITY_ICONS = {"P0": "🔴", "P1": "🟡", "P2": "⚪"}


def render(tasks: list[Task], brief: bool = False) -> str:
    if brief:
        tasks = [t for t in tasks if t.priority == "P0"]

    p0 = [t for t in tasks if t.priority == "P0"]
    overdue = [t for t in tasks if t.overdue]

    width = 72
    bar = "═" * width
    thin = "─" * width

    lines: list[str] = []
    lines.append(bar)
    ts_str = datetime.now().strftime("%Y-%m-%d %H:%M PDT")
    header = f"  VLAD ACTION BOARD — {ts_str}"
    lines.append(header)
    stats = f"  P0={len(p0)}  OVERDUE={len(overdue)}  TOTAL={len(tasks)}"
    lines.append(stats)
    lines.append(bar)

    if not tasks:
        lines.append("  ✅  Nothing needs your attention right now.")
        lines.append(bar)
        return "\n".join(lines)

    # Group by section in defined order
    by_section: dict[str, list[Task]] = {s: [] for s in SECTION_ORDER}
    for t in tasks:
        by_section.setdefault(t.section, []).append(t)

    for section in SECTION_ORDER:
        section_tasks = by_section.get(section, [])
        if not section_tasks:
            continue
        # Sort by priority then overdue
        section_tasks.sort(
            key=lambda t: (PRIORITY_ORDER.get(t.priority, 9), not t.overdue)
        )
        lines.append("")
        lines.append(f"  ▶ {section}")
        lines.append(thin)
        for t in section_tasks:
            icon = PRIORITY_ICONS.get(t.priority, "⚪")
            overdue_tag = "  ⚠️ OVERDUE" if t.overdue else ""
            age_tag = f"  [{t.age_str()}]" if t.age_str() else ""
            lines.append(f"  {icon} [{t.priority}] {t.label}{overdue_tag}{age_tag}")
            if t.detail:
                lines.append(f"       → {t.detail}")
            time_str = f"~{t.est_mins}min" if t.est_mins > 0 else "passive"
            if t.blocked_if_not_done:
                lines.append(f"       ⏱ {time_str} | 🚧 {t.blocked_if_not_done}")
            else:
                lines.append(f"       ⏱ {time_str}")
        lines.append(thin)

    lines.append("")
    if overdue:
        lines.append(f"  ⚠️  {len(overdue)} item(s) have been waiting >{OVERDUE_HOURS}h — act today.")
    if p0:
        total_p0_mins = sum(t.est_mins for t in p0)
        lines.append(f"  🔴 {len(p0)} P0 item(s) · ~{total_p0_mins}min total to clear")
    lines.append(bar)
    return "\n".join(lines)


def render_brief_telegram(tasks: list[Task]) -> str:
    """Compact section for Telegram morning digest. Only actionable items, no fluff."""
    p0 = [t for t in tasks if t.priority == "P0"]
    overdue = [t for t in tasks if t.overdue]

    if not tasks:
        return ""  # no section if nothing to do

    lines = ["", f"📋 *Vlad Action Board* — {len(tasks)} items ({len(p0)} P0, {len(overdue)} overdue)"]

    by_section: dict[str, list[Task]] = {s: [] for s in SECTION_ORDER}
    for t in tasks:
        by_section.setdefault(t.section, []).append(t)

    for section in SECTION_ORDER:
        section_tasks = by_section.get(section, [])
        if not section_tasks:
            continue
        section_tasks.sort(key=lambda t: (PRIORITY_ORDER.get(t.priority, 9), not t.overdue))
        lines.append(f"\n*{section}*")
        for t in section_tasks:
            icon = PRIORITY_ICONS.get(t.priority, "⚪")
            od = " ⚠️OVERDUE" if t.overdue else ""
            age = f" [{t.age_str()}]" if t.age_str() else ""
            lines.append(f"{icon} [{t.priority}]{od} {t.label}{age}")
            if t.blocked_if_not_done and t.priority in ("P0", "P1"):
                lines.append(f"  _blocks: {t.blocked_if_not_done[:80]}_")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# FLAG HEALTH sentinel
# ═══════════════════════════════════════════════════════════════════════════

def write_run_sentinel(task_count: int, p0_count: int) -> None:
    """Append a run.done sentinel so flag_health can probe freshness."""
    try:
        BOARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now().isoformat(timespec="seconds"),
            "event": "run.done",
            "task_count": task_count,
            "p0_count": p0_count,
        }
        with BOARD_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="Output raw JSON")
    ap.add_argument("--brief", action="store_true", help="P0s only (digest embedding)")
    ap.add_argument("--telegram", action="store_true",
                    help="Output compact Telegram-formatted section")
    ap.add_argument("--mark-flag", action="store_true",
                    help="Write RICK_VLAD_ACTIONS_LIVE sentinel and exit 0")
    args = ap.parse_args()

    tasks = gather_all()

    if args.mark_flag:
        write_run_sentinel(len(tasks), sum(1 for t in tasks if t.priority == "P0"))
        print("RICK_VLAD_ACTIONS_LIVE sentinel written.")
        return 0

    if args.json:
        out = {
            "ts": _now().isoformat(timespec="seconds"),
            "p0_count": sum(1 for t in tasks if t.priority == "P0"),
            "overdue_count": sum(1 for t in tasks if t.overdue),
            "total_count": len(tasks),
            "tasks": [t.to_dict() for t in tasks],
        }
        print(json.dumps(out, indent=2))
        write_run_sentinel(len(tasks), out["p0_count"])
        return 0

    if args.telegram:
        print(render_brief_telegram(tasks))
        write_run_sentinel(len(tasks), sum(1 for t in tasks if t.priority == "P0"))
        return 0

    print(render(tasks, brief=args.brief))
    write_run_sentinel(len(tasks), sum(1 for t in tasks if t.priority == "P0"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
