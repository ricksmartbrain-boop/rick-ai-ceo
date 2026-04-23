#!/usr/bin/env python3
"""TIER-3 #29 (generalized daily) — Rick's daily autonomous activity digest.

Posts a single Telegram message to ops-alerts topic each morning 08:00 PT
covering yesterday's autonomous work: workflows shipped/cancelled,
top-cost steps, subagent volume, hot leads in pipeline, drafts pending
review, suppression list growth.

Gives Vlad a single-glance view of what Rick did while he slept + what
needs his attention today. Complements the mailbox-digest (inbox-only
surface) with the workflow-side surface.

Read-only. Never sends an email, never closes a workflow. Visibility
only.

CLI:
  python3 ~/clawd/scripts/rick-activity-digest.py --dry-run
  python3 ~/clawd/scripts/rick-activity-digest.py
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
LOG_FILE = DATA_ROOT / "operations" / "rick-activity-digest.jsonl"
SUPPRESSION_FILE = DATA_ROOT / "mailbox" / "suppression.txt"
DRAFTS_DIR = DATA_ROOT / "mailbox" / "drafts"
TG_SCRIPT = ROOT / "scripts" / "tg-topic.sh"


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


def _count_drafts() -> dict:
    out = {"total": 0, "by_kind": {}}
    if not DRAFTS_DIR.is_dir():
        return out
    try:
        for sub in DRAFTS_DIR.iterdir():
            if not sub.is_dir():
                continue
            kind = sub.name
            n = sum(1 for f in sub.iterdir() if f.is_file() and f.suffix in (".json", ".md"))
            if n:
                out["by_kind"][kind] = n
                out["total"] += n
    except OSError:
        pass
    return out


def _suppression_count() -> int:
    if not SUPPRESSION_FILE.is_file():
        return 0
    try:
        return sum(1 for line in SUPPRESSION_FILE.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.strip().startswith("#"))
    except OSError:
        return 0


def _funnel_24h() -> dict:
    """Inbound → classified → routed → drafted funnel for last 24h.
    Reads today's triage JSONL since that's where router persists state.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    triage_file = DATA_ROOT / "mailbox" / "triage" / f"inbound-{today}.jsonl"
    funnel = {"inbound": 0, "classified": 0, "warm_class": 0,
              "routed": 0, "drafted": 0, "suppressed": 0, "self_send_skipped": 0}
    if not triage_file.is_file():
        return funnel
    warm = {"sales_inquiry", "objection_with_counter", "pricing_question",
            "scheduling_request", "referral_request"}
    try:
        for line in triage_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            funnel["inbound"] += 1
            cls = row.get("classification")
            if cls and cls != "unknown":
                funnel["classified"] += 1
            if cls in warm:
                funnel["warm_class"] += 1
            if row.get("router_ran_at"):
                rr = row.get("router_result") or {}
                action = rr.get("action", "")
                if action == "skip-self-send":
                    funnel["self_send_skipped"] += 1
                elif action in ("unsubscribed", "marked-lost"):
                    funnel["suppressed"] += 1
                elif action == "drafted":
                    funnel["drafted"] += 1
                    funnel["routed"] += 1
                else:
                    funnel["routed"] += 1
    except OSError:
        pass
    return funnel


def gather() -> dict:
    cutoff_24h = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    summary = {"since": cutoff_24h, "ts": _now_iso()}

    try:
        con = connect()
        try:
            rows = con.execute(
                "SELECT status, COUNT(*) AS c FROM workflows "
                "WHERE updated_at >= ? GROUP BY status ORDER BY c DESC",
                (cutoff_24h,),
            ).fetchall()
            summary["workflows_24h"] = {r["status"]: r["c"] for r in rows}

            rows = con.execute(
                "SELECT kind, status, COUNT(*) AS c FROM workflows "
                "WHERE status = 'cancelled' AND updated_at >= ? "
                "GROUP BY kind, status ORDER BY c DESC LIMIT 5",
                (cutoff_24h,),
            ).fetchall()
            summary["cancelled_by_kind"] = [dict(r) for r in rows]

            rows = con.execute(
                "SELECT step_name, ROUND(SUM(cost_usd), 4) AS sum_cost, COUNT(*) AS n "
                "FROM outcomes WHERE created_at >= ? AND cost_usd > 0 "
                "GROUP BY step_name ORDER BY sum_cost DESC LIMIT 5",
                (cutoff_24h,),
            ).fetchall()
            summary["top_cost_steps"] = [dict(r) for r in rows]

            row = con.execute(
                "SELECT ROUND(SUM(cost_usd), 3) AS sum_cost, COUNT(*) AS n "
                "FROM outcomes WHERE created_at >= ?",
                (cutoff_24h,),
            ).fetchone()
            summary["total_cost_24h"] = {"sum_usd": row["sum_cost"] or 0, "outcomes": row["n"] or 0}

            rows = con.execute(
                "SELECT route, COUNT(*) AS n FROM outcomes "
                "WHERE created_at >= ? GROUP BY route ORDER BY n DESC LIMIT 8",
                (cutoff_24h,),
            ).fetchall()
            summary["routes_24h"] = {r["route"]: r["n"] for r in rows}

            try:
                rows = con.execute(
                    "SELECT username, platform, score FROM prospect_pipeline "
                    "WHERE score >= 7 ORDER BY updated_at DESC LIMIT 5"
                ).fetchall()
                summary["hot_prospects"] = [dict(r) for r in rows]
            except Exception:
                summary["hot_prospects"] = []

            try:
                row = con.execute(
                    "SELECT COUNT(*) AS c FROM email_threads WHERE status='active'"
                ).fetchone()
                summary["active_email_threads"] = row["c"] if row else 0
            except Exception:
                summary["active_email_threads"] = 0
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        summary["db_error"] = str(exc)[:200]

    summary["drafts_pending"] = _count_drafts()
    summary["suppression_total"] = _suppression_count()
    summary["funnel"] = _funnel_24h()
    return summary


def render(s: dict) -> str:
    wf = s.get("workflows_24h") or {}
    done = wf.get("done", 0)
    cancelled = wf.get("cancelled", 0)
    active = wf.get("active", 0)

    total_cost = s.get("total_cost_24h", {})
    cost_usd = total_cost.get("sum_usd", 0)
    outcomes_n = total_cost.get("outcomes", 0)

    # TIER-C #7 — cancellation-rate alarm. Surfaces silent issues like the
    # 2026-04-23 self-send classifier bug (100% deal_close cancel rate hid
    # a misclassification, not a healthy filter).
    finished = done + cancelled
    cancel_rate_alarm = ""
    if finished >= 5 and cancelled / finished >= 0.80:
        cancel_rate_alarm = f"🚨 *Cancellation rate {int(100*cancelled/finished)}%* (≥80% triggers alarm)\n"

    lines = [
        f"📊 *Rick daily* — {datetime.now().strftime('%a %b %d')}",
        "",
    ]
    if cancel_rate_alarm:
        lines.append(cancel_rate_alarm)
    lines.extend([
        f"*Workflows 24h*: {done}✅ · {cancelled}❌ · {active}🔄",
        f"*Cost*: ${cost_usd:.3f} across {outcomes_n} outcomes",
    ])

    top = s.get("top_cost_steps") or []
    if top:
        bits = ", ".join(f"{r['step_name']}=${r['sum_cost']:.2f}" for r in top[:3])
        lines.append(f"*Top spend*: {bits}")

    routes = s.get("routes_24h") or {}
    if routes:
        bits = ", ".join(f"{k}={v}" for k, v in list(routes.items())[:5])
        lines.append(f"*Routes*: {bits}")

    threads = s.get("active_email_threads", 0)
    lines.append(f"*Active email threads*: {threads}")

    f = s.get("funnel") or {}
    if f.get("inbound"):
        lines.append("")
        lines.append(
            f"*Inbox funnel*: {f.get('inbound', 0)} in → {f.get('classified', 0)} classified "
            f"→ {f.get('warm_class', 0)} warm → {f.get('drafted', 0)} drafted "
            f"({f.get('suppressed', 0)} suppressed, {f.get('self_send_skipped', 0)} self-send)"
        )
        if f.get("warm_class", 0) > 0 and f.get("drafted", 0) == 0:
            lines.append(f"  ⚠️ {f.get('warm_class')} warm classifications, 0 drafts produced — funnel break")

    drafts = s.get("drafts_pending", {})
    if drafts.get("total", 0) > 0:
        bits = ", ".join(f"{k}={v}" for k, v in drafts.get("by_kind", {}).items())
        lines.append(f"*Drafts pending review*: {drafts['total']} ({bits})")

    hot = s.get("hot_prospects") or []
    if hot:
        lines.append("")
        lines.append("🔥 *Hot prospects* (score ≥ 7):")
        for h in hot[:5]:
            lines.append(f"  • {h.get('platform', '?')}: `{h.get('username', '?')}` — {h.get('score', 0):.1f}/10")

    cbk = s.get("cancelled_by_kind") or []
    if cbk:
        lines.append("")
        lines.append("*Cancelled breakdown*:")
        for r in cbk[:3]:
            lines.append(f"  • {r['kind']}: {r['c']}")

    lines.append("")
    lines.append(f"_Suppression list: {s.get('suppression_total', 0)} entries_")

    return "\n".join(lines)


def post_to_telegram(text: str, dry_run: bool) -> dict:
    if dry_run:
        return {"posted": False, "reason": "dry-run", "preview_chars": len(text)}
    if not TG_SCRIPT.is_file():
        return {"posted": False, "reason": "tg-script-missing"}
    try:
        proc = subprocess.run(
            ["bash", str(TG_SCRIPT), "ops-alerts", text],
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
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--print-only", action="store_true")
    args = ap.parse_args()

    summary = gather()
    text = render(summary)

    dry = args.dry_run or args.print_only
    if not dry and os.getenv("RICK_ACTIVITY_DIGEST_LIVE", "1").strip().lower() not in ("1", "true", "yes"):
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
