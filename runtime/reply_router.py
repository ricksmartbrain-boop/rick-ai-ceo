#!/usr/bin/env python3
"""Phase G router — takes classified inbound replies + routes to the right action.

- sales_inquiry → queue_deal_close_workflow
- objection → delegate to Remy for rebuttal draft
- not_interested → mark closed_lost + suppress
- unsubscribe → suppress + ledger

Idempotent via `router_ran_at` marker. CLI:
  python3 -m runtime.reply_router drain --dry-run --batch 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.db import connect as db_connect  # noqa: E402
from runtime.engine import append_execution_ledger, queue_deal_close_workflow  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
SUPPRESSION = DATA_ROOT / "mailbox" / "suppression.txt"
LOG_FILE = DATA_ROOT / "operations" / "reply-router.jsonl"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_event(event: dict):
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ran_at": now_iso(), **event}) + "\n")
    except OSError:
        pass


def append_suppression(email: str, reason: str):
    """Append an email to the global suppression list. Idempotent (grep first)."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    try:
        SUPPRESSION.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if SUPPRESSION.exists():
            for line in SUPPRESSION.read_text(encoding="utf-8").splitlines():
                s = line.strip().lower()
                if s and not s.startswith("#"):
                    existing.add(s.split()[0] if s.split() else s)
        if email in existing:
            return False
        with SUPPRESSION.open("a", encoding="utf-8") as f:
            f.write(f"{email}  # {reason} {now_iso()}\n")
        return True
    except OSError:
        return False


def dispatch_sales_inquiry(conn, row: dict, dry_run: bool) -> dict:
    email = row.get("from", "").strip()
    body = (row.get("body") or "")[:500]
    if dry_run:
        return {"action": "would-queue-deal_close", "email": email}
    try:
        wf_id = queue_deal_close_workflow(
            conn, email=email, name=row.get("from_name", ""),
            source="reply", message=body,
        )
        return {"action": "queued", "workflow_id": wf_id, "email": email}
    except Exception as exc:
        return {"action": "error", "error": str(exc)[:200], "email": email}


def dispatch_objection(conn, row: dict, dry_run: bool) -> dict:
    email = row.get("from", "").strip()
    body = (row.get("body") or "")[:800]
    if dry_run:
        return {"action": "would-delegate-remy", "email": email}
    # Subagent delegate would route per config/event-reactions.json → remy.
    # Keep this lightweight — just append to ledger so operator can see it land.
    # Subagent dispatch has its own heartbeat + cost risk; don't auto-fire on every
    # objection. Just alert for now.
    try:
        append_execution_ledger(
            "decision",
            f"Objection reply from {email}",
            status="open",
            area="sales",
            project="replies",
            route="writing",
            notes=f"Body: {body[:300]}",
        )
        return {"action": "logged-for-rebuttal", "email": email}
    except Exception as exc:
        return {"action": "error", "error": str(exc)[:200], "email": email}


def dispatch_not_interested(conn, row: dict, dry_run: bool) -> dict:
    email = row.get("from", "").strip()
    if dry_run:
        return {"action": "would-suppress-and-mark-lost", "email": email}
    try:
        added = append_suppression(email, "closed_lost")
        append_execution_ledger(
            "decision",
            f"Closed-lost: {email}",
            status="done",
            area="sales",
            project="replies",
            route="ops",
            notes="not_interested",
        )
        return {"action": "marked-lost", "email": email, "suppressed": added}
    except Exception as exc:
        return {"action": "error", "error": str(exc)[:200], "email": email}


def dispatch_unsubscribe(conn, row: dict, dry_run: bool) -> dict:
    email = row.get("from", "").strip()
    if dry_run:
        return {"action": "would-unsubscribe", "email": email}
    try:
        added = append_suppression(email, "unsubscribed")
        append_execution_ledger(
            "decision",
            f"Unsubscribed: {email}",
            status="done",
            area="compliance",
            project="replies",
            route="ops",
            notes="explicit unsub",
        )
        return {"action": "unsubscribed", "email": email, "suppressed": added}
    except Exception as exc:
        return {"action": "error", "error": str(exc)[:200], "email": email}


def dispatch_objection_with_counter(conn, row: dict, dry_run: bool) -> dict:
    """TIER-3.5 #A4 (2026-04-23) — fire-and-forget counter-pitch drafter.

    Subprocess invocation keeps the router lightweight + isolates failures.
    Drafts land in ~/rick-vault/mailbox/drafts/counter-pitch/. NEVER auto-sends.
    """
    import subprocess  # noqa: WPS433
    thread_id = row.get("thread_id") or row.get("message_id") or ""
    objection_text = (row.get("body") or "")[:2000]
    prospect_id = row.get("prospect_id") or ""
    if dry_run:
        return {"action": "would-draft-counter-pitch", "thread_id": thread_id}
    if not thread_id:
        return {"action": "skip-no-thread-id"}
    script = Path(__file__).resolve().parents[1] / "skills" / "counter-pitch" / "scripts" / "draft-counter.py"
    if not script.is_file():
        return {"action": "skip-script-missing", "path": str(script)}
    try:
        proc = subprocess.run(
            ["python3", str(script),
             "--thread-id", thread_id,
             "--objection-text", objection_text]
            + (["--prospect-id", prospect_id] if prospect_id else []),
            capture_output=True, text=True, timeout=120, check=False,
        )
        if proc.returncode == 0:
            try:
                payload = json.loads(proc.stdout.strip().splitlines()[-1])
                return {"action": "drafted", **payload}
            except (json.JSONDecodeError, IndexError):
                return {"action": "drafted-no-payload", "stdout": proc.stdout[:200]}
        return {"action": "drafter-failed", "exit": proc.returncode, "stderr": proc.stderr[:200]}
    except subprocess.TimeoutExpired:
        return {"action": "drafter-timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"action": "drafter-exception", "error": str(exc)[:200]}


def _alert_vlad(label: str, conn, row: dict, dry_run: bool, *,
                area: str = "customer", urgent: bool = False) -> dict:
    """Generic alert dispatcher — log + execution_ledger entry + Telegram ping.
    Never sends an email or auto-replies. Used for buckets that need Vlad-touch:
    support_request, question, pricing_question, scheduling_request, referral_request.
    """
    email = (row.get("from") or "").strip()
    body_preview = (row.get("body") or "")[:300]
    subject = (row.get("subject") or "")[:120]
    if dry_run:
        return {"action": f"would-alert-vlad", "label": label, "email": email}

    try:
        append_execution_ledger(
            "decision",
            f"{label} from {email}",
            status="open",
            area=area,
            project="replies",
            route="ops",
            notes=f"Subject: {subject} | Body: {body_preview}",
        )
    except Exception as exc:  # noqa: BLE001
        return {"action": "ledger-error", "error": str(exc)[:200]}

    # Telegram ping — best-effort, isolated subprocess so router never blocks
    try:
        import subprocess  # noqa: WPS433
        tg_script = Path(__file__).resolve().parents[1] / "scripts" / "tg-topic.sh"
        if tg_script.is_file():
            prefix = "🚨 " if urgent else ""
            text = f"{prefix}*{label}* from `{email}`\n_{subject}_\n\n{body_preview[:240]}"
            subprocess.run(
                ["bash", str(tg_script), "customer", text],
                capture_output=True, text=True, timeout=10, check=False,
            )
    except Exception:
        pass
    return {"action": "alerted-vlad", "label": label, "email": email}


def dispatch_support_request(conn, row, dry_run):
    return _alert_vlad("support_request", conn, row, dry_run, area="customer", urgent=True)


def dispatch_question(conn, row, dry_run):
    return _alert_vlad("question", conn, row, dry_run, area="sales")


def dispatch_pricing_question(conn, row, dry_run):
    return _alert_vlad("pricing_question", conn, row, dry_run, area="sales", urgent=True)


def dispatch_scheduling_request(conn, row, dry_run):
    return _alert_vlad("scheduling_request", conn, row, dry_run, area="sales", urgent=True)


def dispatch_referral_request(conn, row, dry_run):
    return _alert_vlad("referral_request", conn, row, dry_run, area="sales")


DISPATCHERS = {
    "sales_inquiry": dispatch_sales_inquiry,
    "objection": dispatch_objection,
    "objection_with_counter": dispatch_objection_with_counter,
    "not_interested": dispatch_not_interested,
    "unsubscribe": dispatch_unsubscribe,
    "support_request": dispatch_support_request,
    "question": dispatch_question,
    "pricing_question": dispatch_pricing_question,
    "scheduling_request": dispatch_scheduling_request,
    "referral_request": dispatch_referral_request,
}


def process_file(conn, path: Path, dry_run: bool, batch_cap: int) -> dict:
    if not path.exists():
        return {"file": str(path), "routed": 0}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return {"file": str(path), "routed": 0, "error": "read-failed"}
    rows = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    routed = 0
    summary_by_action = {}
    for row in rows:
        if row.get("router_ran_at"):
            continue
        label = row.get("classification")
        if not label or label not in DISPATCHERS:
            continue
        if routed >= batch_cap:
            break
        result = DISPATCHERS[label](conn, row, dry_run)
        row["router_ran_at"] = now_iso()
        row["router_result"] = result
        summary_by_action[result.get("action", "unknown")] = summary_by_action.get(result.get("action", "unknown"), 0) + 1
        routed += 1
        log_event({"file": path.name, "label": label, **result})
    if routed and not dry_run:
        try:
            path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")
        except OSError:
            pass
    return {"file": str(path), "routed": routed, "by_action": summary_by_action}


def drain(dry_run: bool, batch_cap: int) -> dict:
    TRIAGE_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(TRIAGE_DIR.glob("inbound-*.jsonl"))
    conn = db_connect()
    totals = {"files": 0, "routed": 0, "by_action": {}}
    try:
        for f in files:
            r = process_file(conn, f, dry_run, batch_cap - totals["routed"])
            totals["files"] += 1
            totals["routed"] += r["routed"]
            for k, v in (r.get("by_action") or {}).items():
                totals["by_action"][k] = totals["by_action"].get(k, 0) + v
            if totals["routed"] >= batch_cap:
                break
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"dry_run": dry_run, **totals, "ran_at": now_iso()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=False)
    drain_cmd = sub.add_parser("drain")
    drain_cmd.add_argument("--dry-run", action="store_true", default=True)
    drain_cmd.add_argument("--live", dest="dry_run", action="store_false")
    drain_cmd.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()

    # Even with --live, require master gate
    dry = args.dry_run if args.cmd else True
    if not dry and os.getenv("RICK_REPLY_ROUTER_LIVE") != "1":
        dry = True
    if args.cmd == "drain" or args.cmd is None:
        result = drain(dry, args.batch if args.cmd else 10)
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
