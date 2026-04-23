#!/usr/bin/env python3
"""TIER-3.5 #A11 — daily mailbox digest to ops thread.

Posts a single Telegram digest to the `customer` topic at 09:00 PT each day
covering: new inbound threads, classifier breakdown, drafts pending review,
follow-ups queued, threads needing Vlad attention.

Read-only — never sends an email, never closes a thread, never auto-replies.
Just visibility so Vlad knows what landed.

CLI:
  python3 ~/clawd/scripts/mailbox-digest.py --dry-run
  python3 ~/clawd/scripts/mailbox-digest.py
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts"
TRIAGE_DIR = DATA_ROOT / "mailbox" / "triage"
LOG_FILE = DATA_ROOT / "operations" / "mailbox-digest.jsonl"
TG_SCRIPT = ROOT / "scripts" / "tg-topic.sh"

NEEDS_VLAD_KEYWORDS = ("refund", "lawyer", "chargeback", "cancel my subscription", "complaint", "lawsuit")
NEWSLETTER_FROM_PATTERNS = ("newsletter@", "no-reply@", "noreply@", "news@", "hello@morningbrew",
                            "hello@producthunt", "team@producthunt", "digest@", "updates@")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _log(payload: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload["ts"] = _now_iso()
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def _count_drafts(subdir: str) -> int:
    p = DRAFTS_DIR / subdir
    if not p.is_dir():
        return 0
    try:
        return sum(1 for f in p.iterdir() if f.is_file() and f.suffix == ".json")
    except OSError:
        return 0


def _today_inbound_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    f = TRIAGE_DIR / f"inbound-{today}.jsonl"
    if not f.is_file():
        return 0
    try:
        return sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0


def _scan_needs_vlad() -> list[str]:
    """Last 24h inbound JSONL — flag threads with high-stakes keywords."""
    out: list[str] = []
    today = datetime.now().strftime("%Y-%m-%d")
    yest = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    for day in (yest, today):
        f = TRIAGE_DIR / f"inbound-{day}.jsonl"
        if not f.is_file():
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sender = (row.get("from") or "").lower()
                if any(p in sender for p in NEWSLETTER_FROM_PATTERNS):
                    continue
                head = (row.get("body") or "")[:500].lower()
                if any(k in head for k in NEEDS_VLAD_KEYWORDS):
                    out.append(row.get("from", "?"))
        except OSError:
            continue
    return list(dict.fromkeys(out))[:5]


def gather() -> dict:
    summary = {
        "ts": _now_iso(),
        "today_inbound": _today_inbound_count(),
        "drafts_counter_pitch": _count_drafts("counter-pitch"),
        "drafts_follow_up": _count_drafts("follow-up"),
        "drafts_total": _count_drafts("counter-pitch") + _count_drafts("follow-up"),
        "needs_vlad": _scan_needs_vlad(),
    }
    try:
        con = connect()
        try:
            cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
            cutoff_7d = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")

            row = con.execute(
                "SELECT COUNT(*) AS c FROM email_threads WHERE created_at >= ?", (cutoff_24h,)
            ).fetchone()
            summary["new_threads_24h"] = row["c"] if row else 0

            row = con.execute(
                "SELECT COUNT(*) AS c FROM email_threads WHERE last_inbound_at >= ?", (cutoff_24h,)
            ).fetchone()
            summary["replies_24h"] = row["c"] if row else 0

            row = con.execute(
                "SELECT COUNT(*) AS c FROM email_threads WHERE status='active'"
            ).fetchone()
            summary["active_threads_total"] = row["c"] if row else 0

            try:
                rows = con.execute(
                    "SELECT classification, COUNT(*) AS c FROM email_classifications "
                    "WHERE classified_at >= ? GROUP BY classification ORDER BY c DESC", (cutoff_7d,)
                ).fetchall()
                summary["classifier_7d"] = {r["classification"]: r["c"] for r in rows}
            except Exception:
                summary["classifier_7d"] = {}

            try:
                row = con.execute(
                    "SELECT COUNT(*) AS due FROM follow_up_queue "
                    "WHERE status='pending' AND follow_up_at <= datetime('now')"
                ).fetchone()
                summary["follow_ups_due"] = row["due"] if row else 0
                row = con.execute(
                    "SELECT COUNT(*) AS pend FROM follow_up_queue WHERE status='pending'"
                ).fetchone()
                summary["follow_ups_total_pending"] = row["pend"] if row else 0
            except Exception:
                summary["follow_ups_due"] = 0
                summary["follow_ups_total_pending"] = 0
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        summary["db_error"] = str(exc)[:200]
    return summary


def render(s: dict) -> str:
    lines = [
        f"📬 *Mailbox digest* — {datetime.now().strftime('%a %b %d')}",
        "",
        f"*Today*: {s.get('today_inbound', 0)} inbound · {s.get('new_threads_24h', 0)} new threads · {s.get('replies_24h', 0)} replies (24h)",
        f"*Active threads*: {s.get('active_threads_total', 0)} total",
    ]
    drafts_total = s.get("drafts_total", 0)
    if drafts_total:
        lines.append(
            f"*Drafts pending review*: {drafts_total} "
            f"({s.get('drafts_counter_pitch', 0)} counter-pitch · "
            f"{s.get('drafts_follow_up', 0)} follow-up)"
        )
    follow_due = s.get("follow_ups_due", 0)
    follow_pending = s.get("follow_ups_total_pending", 0)
    if follow_pending:
        lines.append(f"*Follow-ups*: {follow_due} due now · {follow_pending} queued")

    cls = s.get("classifier_7d") or {}
    if cls:
        bits = ", ".join(f"{k}={v}" for k, v in list(cls.items())[:6])
        lines.append(f"*Classifier 7d*: {bits}")

    needs = s.get("needs_vlad") or []
    if needs:
        lines.append("")
        lines.append("⚠️  *Needs Vlad eyes*:")
        for sender in needs:
            lines.append(f"  • {sender}")

    if drafts_total == 0 and not needs:
        lines.append("")
        lines.append("_No drafts pending. No high-stakes flags. Quiet day._")

    return "\n".join(lines)


def post_to_telegram(text: str, dry_run: bool) -> dict:
    if dry_run:
        return {"posted": False, "reason": "dry-run", "preview_chars": len(text)}
    if not TG_SCRIPT.is_file():
        return {"posted": False, "reason": "tg-script-missing"}
    try:
        proc = subprocess.run(
            ["bash", str(TG_SCRIPT), "customer", text],
            capture_output=True, text=True, timeout=20, check=False,
        )
        if proc.returncode == 0:
            return {"posted": True}
        return {"posted": False, "reason": "tg-failed", "stderr": proc.stderr[:200]}
    except subprocess.TimeoutExpired:
        return {"posted": False, "reason": "tg-timeout"}
    except Exception as exc:  # noqa: BLE001
        return {"posted": False, "reason": str(exc)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--print-only", action="store_true",
                    help="Skip Telegram even when not dry-run; print to stdout only")
    args = ap.parse_args()

    summary = gather()
    text = render(summary)

    dry = args.dry_run or args.print_only
    if dry or not os.getenv("RICK_MAILBOX_DIGEST_LIVE", "1").strip().lower() in ("1", "true", "yes"):
        dry = True

    result = post_to_telegram(text, dry)
    summary["telegram"] = result

    print(text)
    print()
    print(json.dumps({"telegram": result, "summary_keys": list(summary.keys())}, indent=2))
    _log(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
