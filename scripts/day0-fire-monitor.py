#!/usr/bin/env python3
"""Day-0 fire monitor for the 21-day multi-touch sequencer.

Tracks qualified_lead outbound_jobs dispatched since a rolling look-back window:
  - dispatch_count  : outbound_jobs rows created (any status)
  - send_count      : jobs whose recipient appears in email-sends.jsonl (status=sent)
  - bounce_count    : jobs whose recipient appears in email-bounces.jsonl (event=bounced)
  - reply_count     : inbound emails from lead addresses in reply-router or triage files
  - deliverability_pct : (send_count - bounce_count) / dispatch_count * 100

Writes one row per run to ~/rick-vault/operations/day0-fire-monitor.jsonl.
Fires notify_operator_deduped when the first reply arrives from any qualified_lead.

Usage:
    python3 scripts/day0-fire-monitor.py             # 24h window (default)
    python3 scripts/day0-fire-monitor.py --hours 2   # 2h window
    python3 scripts/day0-fire-monitor.py --dry-run   # no DB writes / no alert
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DB_PATH   = Path(os.getenv("RICK_RUNTIME_DB_FILE", str(DATA_ROOT / "runtime" / "rick-runtime.db")))
OUT_FILE  = DATA_ROOT / "operations" / "day0-fire-monitor.jsonl"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _load_qualified_lead_jobs(conn, cutoff_iso: str) -> list[dict]:
    """Return email outbound_jobs linked to qualified_lead workflows since cutoff."""
    rows = conn.execute(
        """
        SELECT oj.id AS job_id, oj.lead_id, oj.status AS job_status,
               oj.scheduled_at, oj.created_at, oj.payload_json, oj.result_json,
               w.title AS lead_title, w.context_json AS lead_ctx
        FROM   outbound_jobs oj
        JOIN   workflows w ON w.id = oj.lead_id
        WHERE  oj.channel = 'email'
          AND  w.kind     = 'qualified_lead'
          AND  oj.created_at >= ?
        ORDER  BY oj.created_at ASC
        """,
        (cutoff_iso,),
    ).fetchall()

    leads = []
    for r in rows:
        payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        ctx     = json.loads(r["lead_ctx"]) if r["lead_ctx"] else {}
        email   = (payload.get("to") or ctx.get("email") or "").lower().strip()
        leads.append(
            {
                "job_id":       r["job_id"],
                "wf_id":        r["lead_id"],
                "lead_title":   r["lead_title"],
                "email":        email,
                "job_status":   r["job_status"],
                "scheduled_at": r["scheduled_at"],
                "created_at":   r["created_at"],
            }
        )
    return leads


def _load_sent_emails(cutoff_iso: str) -> set[str]:
    """Return lowercased recipient emails confirmed sent (email-sends.jsonl)."""
    sent: set[str] = set()
    path = DATA_ROOT / "operations" / "email-sends.jsonl"
    if not path.exists():
        return sent
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("status") == "sent" and r.get("ts", "") >= cutoff_iso:
                to = r.get("to", "").lower().strip()
                if to:
                    sent.add(to)
        except (json.JSONDecodeError, KeyError):
            pass
    return sent


def _load_bounced_emails(cutoff_iso: str) -> dict[str, str]:
    """Return {email: bounce_ts} for bounces in email-bounces.jsonl since cutoff."""
    bounced: dict[str, str] = {}
    path = DATA_ROOT / "operations" / "email-bounces.jsonl"
    if not path.exists():
        return bounced
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("event") == "bounced" and r.get("ts", "") >= cutoff_iso:
                to = r.get("to", "").lower().strip()
                if to:
                    bounced[to] = r.get("ts", "")
        except (json.JSONDecodeError, KeyError):
            pass
    return bounced


def _load_reply_emails(cutoff_iso: str) -> dict[str, dict]:
    """Return {from_email: {label, ts, source}} for inbound replies since cutoff.

    Sources checked (read-only):
      1. operations/reply-router.jsonl  — router action log
      2. mailbox/triage/inbound-TODAY.jsonl
      3. mailbox/triage/inbound-YESTERDAY.jsonl
    """
    replies: dict[str, dict] = {}
    triage_dir = DATA_ROOT / "mailbox" / "triage"

    # 1. reply-router.jsonl
    rr_path = DATA_ROOT / "operations" / "reply-router.jsonl"
    if rr_path.exists():
        for line in rr_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = r.get("ran_at", "")
                if ts >= cutoff_iso:
                    em = r.get("email", "").lower().strip()
                    if em and em not in replies:
                        replies[em] = {
                            "label":  r.get("label", ""),
                            "ts":     ts,
                            "source": "reply-router",
                        }
            except (json.JSONDecodeError, KeyError):
                pass

    # 2 & 3. Triage inbound files for today + yesterday
    today     = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    for date_str in (today, yesterday):
        tfile = triage_dir / f"inbound-{date_str}.jsonl"
        if not tfile.exists():
            continue
        for line in tfile.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = r.get("classified_at") or r.get("ingested_at", "")
                if ts >= cutoff_iso:
                    em = r.get("from", "").lower().strip()
                    if em and em not in replies:
                        replies[em] = {
                            "label":  r.get("classification", ""),
                            "ts":     ts,
                            "source": f"inbound-{date_str}",
                        }
            except (json.JSONDecodeError, KeyError):
                pass

    return replies


# ---------------------------------------------------------------------------
# Alert helper
# ---------------------------------------------------------------------------

def _fire_first_reply_alert(
    conn,
    lead_title: str,
    lead_email: str,
    wf_id: str,
    label: str,
    dry_run: bool,
) -> str:
    """Send notify_operator_deduped for the first sequencer reply. Returns result string."""
    if dry_run:
        return "dry-run"
    try:
        from runtime.engine import notify_operator_deduped  # noqa: PLC0415
    except ImportError:
        return "import-error"

    text = (
        f"🎯 FIRST REPLY: {lead_title} ({lead_email}) replied to sequencer email "
        f"— label: {label} | wf: {wf_id}"
    )
    result = notify_operator_deduped(
        conn,
        text,
        kind="sequencer_first_reply",
        dedup_window_hours=168,          # 1-week dedup per effective message hash
        workflow_id=wf_id,
        lane="outreach",
        purpose="revenue",               # bypasses dedup (URGENT_PURPOSES list)
    )
    return result


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def run(hours: int = 24, dry_run: bool = False) -> dict:
    import sqlite3

    cutoff_dt  = datetime.utcnow() - timedelta(hours=hours)
    cutoff_iso = cutoff_dt.isoformat(timespec="seconds")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    try:
        leads     = _load_qualified_lead_jobs(conn, cutoff_iso)
        sent_set  = _load_sent_emails(cutoff_iso)
        bounce_map = _load_bounced_emails(cutoff_iso)
        reply_map = _load_reply_emails(cutoff_iso)

        # Per-lead enrichment
        per_lead: list[dict] = []
        first_reply_alert_result = "none"

        for job in leads:
            email = job["email"]
            is_sent     = email in sent_set
            is_bounced  = email in bounce_map
            reply_info  = reply_map.get(email)
            has_reply   = reply_info is not None

            entry = {
                "job_id":       job["job_id"],
                "wf_id":        job["wf_id"],
                "lead_title":   job["lead_title"],
                "email":        email,
                "job_status":   job["job_status"],
                "scheduled_at": job["scheduled_at"],
                "sent":         is_sent,
                "bounced":      is_bounced,
                "bounce_ts":    bounce_map.get(email),
                "replied":      has_reply,
                "reply_label":  reply_info["label"] if reply_info else None,
                "reply_ts":     reply_info["ts"] if reply_info else None,
                "reply_source": reply_info["source"] if reply_info else None,
            }
            per_lead.append(entry)

            # Fire alert on first reply encountered
            if has_reply and first_reply_alert_result == "none":
                first_reply_alert_result = _fire_first_reply_alert(
                    conn,
                    job["lead_title"],
                    email,
                    job["wf_id"],
                    reply_info["label"],
                    dry_run,
                )

        # Aggregate metrics
        dispatch_count = len(leads)
        send_count     = sum(1 for l in per_lead if l["sent"])
        bounce_count   = sum(1 for l in per_lead if l["bounced"])
        reply_count    = sum(1 for l in per_lead if l["replied"])
        delivered      = send_count - bounce_count
        deliverability_pct = (
            round(delivered / dispatch_count * 100, 1) if dispatch_count > 0 else None
        )

        row = {
            "ts":                 _now_iso(),
            "window_hours":       hours,
            "cutoff_iso":         cutoff_iso,
            "dispatch_count":     dispatch_count,
            "send_count":         send_count,
            "bounce_count":       bounce_count,
            "reply_count":        reply_count,
            "delivered_count":    delivered,
            "deliverability_pct": deliverability_pct,
            "alert_result":       first_reply_alert_result,
            "per_lead":           per_lead,
        }

        # Write to JSONL
        if not dry_run:
            OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
            with OUT_FILE.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

        return row

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Day-0 multi-touch sequencer fire monitor")
    parser.add_argument("--hours",   type=int, default=24, help="Look-back window in hours (default 24)")
    parser.add_argument("--dry-run", action="store_true",  help="Don't write output or send alerts")
    args = parser.parse_args()

    result = run(hours=args.hours, dry_run=args.dry_run)

    # Human-readable summary to stdout
    mode = "[DRY-RUN] " if args.dry_run else ""
    d = result
    print(
        f"{mode}Day-0 monitor | window={d['window_hours']}h | "
        f"dispatched={d['dispatch_count']} sent={d['send_count']} "
        f"bounced={d['bounce_count']} replied={d['reply_count']} "
        f"deliverability={d['deliverability_pct']}%"
    )
    if d["per_lead"]:
        print("\nPer-lead breakdown:")
        for l in d["per_lead"]:
            tags = []
            if l["sent"]:    tags.append("SENT")
            if l["bounced"]: tags.append("BOUNCED")
            if l["replied"]: tags.append(f"REPLIED({l['reply_label']})")
            status_str = " | ".join(tags) if tags else f"job={l['job_status']}"
            print(f"  {l['lead_title']} <{l['email']}> — {status_str}")
    else:
        print("  (no qualified_lead email jobs found in window)")

    if d["alert_result"] not in ("none", "dry-run"):
        print(f"\nFirst-reply alert: {d['alert_result']}")

    if not args.dry_run:
        print(f"\nWritten → {OUT_FILE}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
