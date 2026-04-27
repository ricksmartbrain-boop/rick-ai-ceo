#!/usr/bin/env python3
"""Read-only daily leverage meter for Rick.

Computes today's autonomous-hours-equivalent from concrete actions in the
workspace / vault:
- emails sent + reply drafts
- leads qualified + workflow progress
- voice calls placed
- content posted across channels
- bounce / suppression maintenance
- workflow reaping / stuck cleanup
- inbound classification + routing

Also surfaces a simple persona audit and a short P1 automation-gap backlog.

Never mutates business state. Safe to import from the daily activity digest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from runtime.db import connect  # noqa: E402

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
OPS_ROOT = DATA_ROOT / "operations"
MAILBOX_ROOT = DATA_ROOT / "mailbox"
DRAFTS_DIR = MAILBOX_ROOT / "drafts"
TRIAGE_DIR = MAILBOX_ROOT / "triage"
SUBAGENTS_FILE = ROOT / "config" / "subagents.json"
EXECUTION_LEDGER = OPS_ROOT / "execution-ledger.jsonl"
EMAIL_SENDS = OPS_ROOT / "email-sends.jsonl"
OUTBOUND_DISPATCHER = OPS_ROOT / "outbound-dispatcher.jsonl"
REPLY_ROUTER = OPS_ROOT / "reply-router.jsonl"
BOUNCE_LOG = OPS_ROOT / "email-bounces.jsonl"
VOICE_CALLS = OPS_ROOT / "elevenlabs-calls.jsonl"
SUPPRESSION_FILE = MAILBOX_ROOT / "suppression.txt"

SOCIAL_CHANNELS = {"linkedin", "threads", "instagram", "moltbook"}

# Typical-human time costs in minutes.
COSTS = {
    "emails_sent": 5,
    "reply_drafts": 8,
    "leads_qualified": 10,
    "workflows_progressed": 10,
    "voice_calls_placed": 8,
    "content_posts": 15,
    "bounces_and_suppression": 4,
    "workflows_reaped": 12,
    "inbound_routed": 6,
}


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return rows
    return rows


def _is_today(ts: str | None, day: str) -> bool:
    return bool(ts) and str(ts).startswith(day)


def _count_reply_drafts(day: str) -> dict[str, Any]:
    count = 0
    files: list[str] = []
    if not DRAFTS_DIR.is_dir():
        return {"count": 0, "files": []}
    try:
        for sub in DRAFTS_DIR.iterdir():
            if not sub.is_dir():
                continue
            for path in sub.iterdir():
                if not path.is_file() or path.suffix != ".json":
                    continue
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                created_at = str(payload.get("created_at", ""))
                if not _is_today(created_at, day):
                    # Fall back to file mtime when metadata is missing or stale.
                    if datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d") != day:
                        continue
                if payload.get("original_reply_body"):
                    count += 1
                    files.append(str(path))
    except OSError:
        pass
    return {"count": count, "files": files}


def _count_email_sends(day: str) -> int:
    rows = _read_jsonl(EMAIL_SENDS)
    return sum(1 for row in rows if row.get("status") == "sent" and _is_today(str(row.get("ts", "")), day))


def _count_leads_qualified(day: str) -> int:
    con = connect()
    try:
        row = con.execute(
            "SELECT COUNT(*) AS c FROM workflows "
            "WHERE kind='qualified_lead' AND status='done' AND updated_at >= ?",
            (f"{day}T00:00:00",),
        ).fetchone()
        return int(row["c"] or 0) if row else 0
    finally:
        con.close()


def _count_workflows_progressed(day: str) -> dict[str, Any]:
    con = connect()
    try:
        rows = con.execute(
            "SELECT kind, COUNT(*) AS c FROM workflows "
            "WHERE status='done' AND kind <> 'qualified_lead' AND updated_at >= ? "
            "GROUP BY kind ORDER BY c DESC",
            (f"{day}T00:00:00",),
        ).fetchall()
        count = sum(int(r["c"] or 0) for r in rows)
        return {"count": count, "by_kind": {r["kind"]: int(r["c"] or 0) for r in rows}}
    finally:
        con.close()


def _count_voice_calls(day: str) -> dict[str, Any]:
    rows = _read_jsonl(VOICE_CALLS)
    seen: set[str] = set()
    for row in rows:
        ts = str(row.get("ts", ""))
        if not _is_today(ts, day):
            continue
        status = str(row.get("status", ""))
        if status not in {"initiated", "completed", "dry_run"}:
            continue
        key = row.get("conversation_id") or row.get("call_sid") or row.get("lead_id") or row.get("phone")
        if key:
            seen.add(str(key))
    return {"count": len(seen), "calls": sorted(seen)}


def _count_content_posts(day: str) -> dict[str, Any]:
    rows = _read_jsonl(OUTBOUND_DISPATCHER)
    by_channel: Counter[str] = Counter()
    for row in rows:
        if str(row.get("status", "")) != "sent":
            continue
        if not _is_today(str(row.get("ran_at", "")), day):
            continue
        ch = str(row.get("channel", ""))
        if ch in SOCIAL_CHANNELS:
            by_channel[ch] += 1
    return {"count": sum(by_channel.values()), "by_channel": dict(by_channel)}


def _count_bounce_and_suppression(day: str) -> dict[str, Any]:
    bounce_rows = _read_jsonl(BOUNCE_LOG)
    router_rows = _read_jsonl(REPLY_ROUTER)
    bounce_events = 0
    suppression_actions = 0

    for row in bounce_rows:
        if not _is_today(str(row.get("ts", "")), day):
            continue
        event = str(row.get("event", ""))
        if event in {"bounced", "complained"}:
            bounce_events += 1
        if int(row.get("new_suppressed", 0) or 0) > 0:
            suppression_actions += int(row.get("new_suppressed", 0) or 0)

    for row in router_rows:
        if not _is_today(str(row.get("ran_at", "")), day):
            continue
        action = str((row.get("router_result") or {}).get("action") or row.get("action", ""))
        if action in {"unsubscribed", "marked-lost"}:
            suppression_actions += 1

    total = bounce_events + suppression_actions
    return {
        "count": total,
        "bounces": bounce_events,
        "suppression_actions": suppression_actions,
    }


def _count_reaped_and_stuck(day: str) -> dict[str, Any]:
    rows = _read_jsonl(EXECUTION_LEDGER)
    keywords = ("reap", "reaped", "stuck", "ghost", "cleanup", "cleaned")
    matches = 0
    examples: list[str] = []
    for row in rows:
        if not _is_today(str(row.get("timestamp", row.get("ts", ""))), day):
            continue
        blob = json.dumps(row, sort_keys=True).lower()
        if any(word in blob for word in keywords):
            matches += 1
            if len(examples) < 3:
                examples.append(str(row.get("title") or row.get("kind") or row.get("notes") or ""))
    return {"count": matches, "examples": examples}


def _count_inbound_routed(day: str) -> dict[str, Any]:
    tfile = TRIAGE_DIR / f"inbound-{day}.jsonl"
    if not tfile.exists():
        return {"count": 0, "labels": {}}
    routed = 0
    labels: Counter[str] = Counter()
    try:
        for line in tfile.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not _is_today(str(row.get("classified_at", "")), day):
                continue
            if row.get("router_result"):
                routed += 1
                labels[str(row.get("classification", "unknown"))] += 1
    except OSError:
        pass
    return {"count": routed, "labels": dict(labels)}


def _persona_audit(day: str) -> dict[str, Any]:
    try:
        config = json.loads(SUBAGENTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        config = {"subagents": {}}

    con = connect()
    try:
        today_rows = con.execute(
            "SELECT kind, COUNT(*) AS c FROM subagent_heartbeat "
            "WHERE finished_at >= ? GROUP BY kind",
            (f"{day}T00:00:00",),
        ).fetchall()
        week_rows = con.execute(
            "SELECT kind, COUNT(*) AS c FROM subagent_heartbeat "
            "WHERE finished_at >= ? GROUP BY kind",
            ((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00"),),
        ).fetchall()
    finally:
        con.close()

    today_counts = {r["kind"]: int(r["c"] or 0) for r in today_rows}
    week_counts = {r["kind"]: int(r["c"] or 0) for r in week_rows}

    firing_today: list[str] = []
    warm_idle: list[str] = []
    dormant_no_evidence: list[str] = []

    for key, payload in (config.get("subagents") or {}).items():
        if not payload.get("active", False):
            continue
        runs_today = today_counts.get(key, 0)
        runs_week = week_counts.get(key, 0)
        if runs_today > 0:
            firing_today.append(f"{payload.get('name', key)} ({runs_today})")
        elif runs_week > 0:
            warm_idle.append(f"{payload.get('name', key)} ({runs_week} / 7d, 0 today)")
        else:
            dormant_no_evidence.append(payload.get("name", key))

    return {
        "firing_today": firing_today,
        "warm_idle": warm_idle,
        "dormant_no_evidence": dormant_no_evidence,
        "today_counts": today_counts,
        "week_counts": week_counts,
    }


def _automation_gaps() -> list[dict[str, str]]:
    return [
        {
            "gap": "Moltbook DM triage is still Vlad-only manual.",
            "path": "Add a Moltbook inbox watcher that pulls new DMs, classifies them into the existing reply-router, and writes draft replies for review.",
        },
        {
            "gap": "Memelord credit top-up is manual.",
            "path": "Add a threshold monitor for remaining credits with a guarded auto-recharge or ops-alert before the floor is hit.",
        },
        {
            "gap": "Bounce-source root-cause analysis is manual.",
            "path": "Enrich bounce polling with provider error codes, campaign/job IDs, and domain-level suppression writeback so repeated failures auto-summarize.",
        },
    ]


def _bucket(minutes: int, count: int) -> dict[str, Any]:
    total_minutes = minutes * count
    return {
        "count": count,
        "minutes_per": minutes,
        "minutes": total_minutes,
        "hours": round(total_minutes / 60, 2),
    }


def compute_leverage(day: str | None = None) -> dict[str, Any]:
    day = day or _today_str()

    emails_sent = _count_email_sends(day)
    reply_drafts = _count_reply_drafts(day)["count"]
    leads_qualified = _count_leads_qualified(day)
    workflows_progressed = _count_workflows_progressed(day)
    voice_calls = _count_voice_calls(day)["count"]
    content_posts = _count_content_posts(day)
    bounces = _count_bounce_and_suppression(day)
    reaped = _count_reaped_and_stuck(day)
    inbound = _count_inbound_routed(day)

    breakdown = {
        "emails_sent": _bucket(COSTS["emails_sent"], emails_sent),
        "reply_drafts": _bucket(COSTS["reply_drafts"], reply_drafts),
        "leads_qualified": _bucket(COSTS["leads_qualified"], leads_qualified),
        "workflows_progressed": _bucket(COSTS["workflows_progressed"], workflows_progressed["count"]),
        "voice_calls_placed": _bucket(COSTS["voice_calls_placed"], voice_calls),
        "content_posts": _bucket(COSTS["content_posts"], content_posts["count"]),
        "bounces_and_suppression": _bucket(COSTS["bounces_and_suppression"], bounces["count"]),
        "workflows_reaped": _bucket(COSTS["workflows_reaped"], reaped["count"]),
        "inbound_routed": _bucket(COSTS["inbound_routed"], inbound["count"]),
    }
    total_minutes = sum(item["minutes"] for item in breakdown.values())
    autonomous_hours = round(total_minutes / 60, 2)
    person_days = round(autonomous_hours / 8, 2)

    persona = _persona_audit(day)

    return {
        "date": day,
        "as_of": _now_iso(),
        "total_minutes": total_minutes,
        "autonomous_hours": autonomous_hours,
        "person_days": person_days,
        "breakdown": breakdown,
        "signals": {
            "emails_sent": emails_sent,
            "reply_drafts": reply_drafts,
            "leads_qualified": leads_qualified,
            "workflows_progressed": workflows_progressed,
            "voice_calls": voice_calls,
            "content_posts": content_posts,
            "bounces": bounces,
            "reaped": reaped,
            "inbound": inbound,
        },
        "persona_audit": persona,
        "automation_gaps": _automation_gaps(),
    }


def render(leverage: dict[str, Any]) -> str:
    lines = [
        f"🚀 Rick leverage 24h: {leverage['autonomous_hours']:.1f} autonomous-hours = {leverage['person_days']:.2f} person-days at human pace",
    ]

    firing = leverage.get("persona_audit", {}).get("firing_today", [])
    if firing:
        lines.append(f"  • Firing: {', '.join(firing)}")

    dormant = leverage.get("persona_audit", {}).get("dormant_no_evidence", [])
    warm_idle = leverage.get("persona_audit", {}).get("warm_idle", [])
    dormant_bits = []
    if warm_idle:
        dormant_bits.append(f"warm-idle: {', '.join(warm_idle)}")
    if dormant:
        dormant_bits.append(f"dormant: {', '.join(dormant)}")
    if dormant_bits:
        lines.append(f"  • Persona audit: {'; '.join(dormant_bits)}")

    gaps = leverage.get("automation_gaps", [])
    if gaps:
        gap_bits = "; ".join(g.get("gap", "") for g in gaps[:3])
        lines.append(f"  • P1 gaps: {gap_bits}")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="YYYY-MM-DD override (defaults to today)")
    ap.add_argument("--json", action="store_true", help="Emit JSON only")
    args = ap.parse_args()

    leverage = compute_leverage(args.date)
    if args.json:
        print(json.dumps(leverage, indent=2, sort_keys=True))
    else:
        print(render(leverage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
