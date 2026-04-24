#!/usr/bin/env python3
"""Proactive messaging for Rick v6 — scheduled messages, reactive alerts, delegation result relay."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("rick.proactive")

DATA_ROOT = Path(os.getenv("RICK_DATA_ROOT", str(Path.home() / "rick-vault")))
SUBAGENT_LOG_DIR = DATA_ROOT / "operations" / "subagent-runs"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cron_matches_now(expression: str) -> bool:
    """Simple cron matching: supports 'H M * * DOW' format.

    H = hour (0-23), M = minute (0-59), DOW = day-of-week (0=Mon..6=Sun or '*').
    Only checks hour and DOW for simplicity (heartbeat runs every 30min).
    """
    parts = expression.strip().split()
    if len(parts) < 5:
        return False
    now = datetime.now()
    minute_spec, hour_spec = parts[0], parts[1]
    dow_spec = parts[4]

    # Check hour
    if hour_spec != "*":
        try:
            if now.hour != int(hour_spec):
                return False
        except ValueError:
            return False

    # Check minute (within 30-min window since heartbeat is ~30min)
    if minute_spec != "*":
        try:
            target_min = int(minute_spec)
            if abs(now.minute - target_min) > 15:
                return False
        except ValueError:
            pass

    # Check day of week (0=Mon)
    if dow_spec != "*":
        try:
            days = [int(d) for d in dow_spec.split(",")]
            if now.weekday() not in days:
                return False
        except ValueError:
            return False

    return True


def seed_default_schedules(connection: sqlite3.Connection) -> None:
    """Insert default scheduled messages if not present."""
    defaults = [
        ("morning_brief", "ceo-hq", "0 8 * * 0,1,2,3,4", "morning_brief"),
        ("ops_daily_metrics", "ops-alerts", "0 9 * * *", "ops_daily_metrics"),
        ("weekly_summary", "ceo-hq", "0 10 * * 0", "weekly_summary"),
        ("approval_reminder", "approvals", "0 10 * * *", "approval_reminder"),
    ]
    for key, topic, cron, template in defaults:
        connection.execute(
            """
            INSERT OR IGNORE INTO scheduled_messages (schedule_key, topic_key, cron_expression, template_key, enabled)
            VALUES (?, ?, ?, ?, 1)
            """,
            (key, topic, cron, template),
        )
    connection.commit()


def render_scheduled_template(connection: sqlite3.Connection, template_key: str) -> str | None:
    """Render a scheduled message template into text."""
    now = datetime.now()

    if template_key == "morning_brief":
        wf_count = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE status IN ('queued','active','blocked','launch-ready','publishing')"
        ).fetchone()["c"]
        queued = connection.execute("SELECT COUNT(*) AS c FROM jobs WHERE status = 'queued'").fetchone()["c"]
        blocked = connection.execute("SELECT COUNT(*) AS c FROM jobs WHERE status = 'blocked'").fetchone()["c"]
        approvals = connection.execute("SELECT COUNT(*) AS c FROM approvals WHERE status = 'open'").fetchone()["c"]
        # Fiverr pipeline counts
        fiverr_orders = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'fiverr_order' AND status IN ('queued','active','blocked')"
        ).fetchone()["c"]
        fiverr_inquiries = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'fiverr_inquiry' AND status IN ('queued','active','blocked')"
        ).fetchone()["c"]
        fiverr_line = ""
        if fiverr_orders or fiverr_inquiries:
            fiverr_line = f"\nFiverr: {fiverr_orders} orders, {fiverr_inquiries} inquiries"
        # Upwork pipeline counts
        upwork_contracts = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'upwork_contract' AND status IN ('queued','active','blocked')"
        ).fetchone()["c"]
        upwork_proposals = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'upwork_proposal' AND status IN ('queued','active','blocked')"
        ).fetchone()["c"]
        upwork_messages = connection.execute(
            "SELECT COUNT(*) AS c FROM workflows WHERE kind = 'upwork_message' AND status IN ('queued','active','blocked')"
        ).fetchone()["c"]
        upwork_line = ""
        if upwork_contracts or upwork_proposals or upwork_messages:
            upwork_line = f"\nUpwork: {upwork_contracts} contracts, {upwork_proposals} proposals, {upwork_messages} messages"
        return (
            f"Good morning. {now.strftime('%A %B %d')}.\n"
            f"Active workflows: {wf_count}\n"
            f"Queued jobs: {queued} | Blocked: {blocked}\n"
            f"Open approvals: {approvals}"
            f"{fiverr_line}"
            f"{upwork_line}"
        )

    if template_key == "ops_daily_metrics":
        from runtime.llm import daily_spend_usd, _get_daily_cap
        spent = daily_spend_usd()
        cap = _get_daily_cap()
        return f"Ops metrics — LLM spend: ${spent:.2f}/${cap:.0f}"

    if template_key == "weekly_summary":
        outcomes = connection.execute(
            "SELECT outcome_type, COUNT(*) AS c FROM outcomes WHERE created_at >= datetime('now', '-7 days') GROUP BY outcome_type"
        ).fetchall()
        counts = {row["outcome_type"]: row["c"] for row in outcomes}
        successes = counts.get("success", 0)
        failures = counts.get("failure", 0)
        return (
            f"Weekly summary ({now.strftime('%B %d')}):\n"
            f"Outcomes: {successes} successes, {failures} failures\n"
            f"Success rate: {round(successes / max(1, successes + failures) * 100)}%"
        )

    if template_key == "approval_reminder":
        open_approvals = connection.execute(
            "SELECT id, request_text, created_at FROM approvals WHERE status = 'open' ORDER BY created_at ASC"
        ).fetchall()
        if not open_approvals:
            return None  # Nothing to send
        lines = [f"Open approvals ({len(open_approvals)}):"]
        for row in open_approvals[:5]:
            lines.append(f"  {row['id']}: {row['request_text'][:80]}")
        return "\n".join(lines)

    return None


def check_scheduled_messages(connection: sqlite3.Connection) -> list[str]:
    """Check and send due scheduled messages. Returns list of sent schedule_keys."""
    sent = []
    try:
        rows = connection.execute(
            "SELECT schedule_key, topic_key, cron_expression, template_key, last_sent_at FROM scheduled_messages WHERE enabled = 1"
        ).fetchall()
    except Exception as exc:
        _log.error("Failed to query scheduled_messages: %s", exc)
        return sent

    for row in rows:
        key = row["schedule_key"]
        last_sent = row["last_sent_at"] or ""

        # Skip if already sent today
        today = datetime.now().strftime("%Y-%m-%d")
        if last_sent.startswith(today):
            continue

        if not _cron_matches_now(row["cron_expression"]):
            continue

        text = render_scheduled_template(connection, row["template_key"])
        if not text:
            continue

        # Send via engine's send_telegram_message (import lazily to avoid circular)
        try:
            from runtime.engine import send_telegram_message
            from runtime.telegram_topics import resolve_notification_target
            target = resolve_notification_target(connection, topic_key=row["topic_key"])
            if target:
                send_telegram_message(
                    connection, text,
                    purpose="scheduled",
                    chat_id=str(target.chat_id),
                    thread_id=target.thread_id,
                )
        except Exception as exc:
            from runtime.log import get_logger
            get_logger("rick.proactive").error("Scheduled message '%s' failed: %s", key, exc)
            continue

        connection.execute(
            "UPDATE scheduled_messages SET last_sent_at = ? WHERE schedule_key = ?",
            (_now_iso(), key),
        )
        connection.commit()
        sent.append(key)

    return sent


def check_reactive_alerts(connection: sqlite3.Connection) -> list[str]:
    """Check for conditions that warrant proactive alerts. Returns list of alert descriptions."""
    alerts = []
    now = datetime.now()

    # Blocked jobs > 30 min
    try:
        blocked = connection.execute(
            """
            SELECT j.id, j.step_name, j.workflow_id, j.updated_at
            FROM jobs j
            WHERE j.status = 'blocked'
            AND j.updated_at <= datetime('now', '-30 minutes')
            """
        ).fetchall()
        for job in blocked:
            alert_key = f"blocked:{job['id']}"
            # 2026-04-24: switched from 30-min notification_log lookback (which
            # let the SAME stuck job page 48 times/day, then 450 times in a
            # week → Vlad muted Rick) to 24h cross-job dedup via
            # notify_operator_deduped. Same normalized error → 1 ping per 24h
            # max, with "(suppressed xN in last 24h)" prefix on the next
            # eligible send. URGENT bypasses dedup.
            text = f"Job {job['id']} ({job['step_name']}) blocked for >30min in workflow {job['workflow_id']}"
            try:
                from runtime.engine import notify_operator_deduped
                from runtime.telegram_topics import resolve_notification_target
                target = resolve_notification_target(connection, topic_key="ops-alerts")
                result = notify_operator_deduped(
                    connection, text,
                    kind="blocked_job",
                    dedup_window_hours=24,
                    workflow_id=job["workflow_id"],
                    purpose="ops",
                    chat_id=str(target.chat_id) if target else "",
                    thread_id=target.thread_id if target else None,
                )
                if result in ("sent_first", "sent_with_count"):
                    alerts.append(alert_key)
            except Exception as exc:
                _log.error("Failed to send stale-job alert: %s", exc)
    except Exception as exc:
        _log.error("Stale job alert check failed: %s", exc)

    # Open approvals > 1 hour
    try:
        old_approvals = connection.execute(
            """
            SELECT id, request_text, workflow_id
            FROM approvals
            WHERE status = 'open'
            AND created_at <= datetime('now', '-1 hour')
            """
        ).fetchall()
        for apr in old_approvals:
            alert_key = f"approval:{apr['id']}"
            already = connection.execute(
                "SELECT 1 FROM notification_log WHERE purpose = ? AND created_at >= datetime('now', '-30 minutes') LIMIT 1",
                (alert_key,),
            ).fetchone()
            if not already:
                text = f"Approval {apr['id']} pending >1hr: {apr['request_text'][:100]}"
                try:
                    from runtime.engine import send_telegram_message
                    from runtime.telegram_topics import resolve_notification_target
                    target = resolve_notification_target(connection, topic_key="approvals")
                    if target:
                        send_telegram_message(
                            connection, text,
                            workflow_id=apr["workflow_id"],
                            purpose=alert_key,
                            chat_id=str(target.chat_id),
                            thread_id=target.thread_id,
                        )
                        alerts.append(alert_key)
                except Exception as exc:
                    _log.error("Failed to send approval alert: %s", exc)
    except Exception as exc:
        _log.error("Approval alert check failed: %s", exc)

    # Fiverr orders approaching deadline (<12h remaining)
    try:
        fiverr_orders = connection.execute(
            """
            SELECT w.id, w.title, w.context_json, w.created_at
            FROM workflows w
            WHERE w.kind = 'fiverr_order'
              AND w.status IN ('queued', 'active', 'blocked')
            """
        ).fetchall()
        for order in fiverr_orders:
            alert_key = f"fiverr_deadline:{order['id']}"
            already = connection.execute(
                "SELECT 1 FROM notification_log WHERE purpose = ? AND created_at >= datetime('now', '-6 hours') LIMIT 1",
                (alert_key,),
            ).fetchone()
            if already:
                continue
            try:
                import json as _json
                ctx = _json.loads(order["context_json"]) if order["context_json"] else {}
                deadline_hours = int(ctx.get("deadline_hours", 72))
                from datetime import datetime as _dt, timedelta as _td
                created = _dt.fromisoformat(order["created_at"])
                deadline = created + _td(hours=deadline_hours)
                remaining = (deadline - now).total_seconds() / 3600
                if 0 < remaining < 12:
                    text = f"Fiverr order {order['id']} due in {remaining:.1f}h: {order['title'][:60]}"
                    from runtime.engine import send_telegram_message
                    from runtime.telegram_topics import resolve_notification_target
                    target = resolve_notification_target(connection, topic_key="ops-alerts")
                    if target:
                        send_telegram_message(
                            connection, text,
                            workflow_id=order["id"],
                            purpose=alert_key,
                            chat_id=str(target.chat_id),
                            thread_id=target.thread_id,
                        )
                        alerts.append(alert_key)
            except Exception as exc:
                _log.error("Failed to check fiverr deadline for %s: %s", order["id"], exc)
    except Exception as exc:
        _log.error("Fiverr deadline check failed: %s", exc)

    # Upwork contracts approaching deadline (<24h remaining)
    try:
        upwork_contracts = connection.execute(
            """
            SELECT w.id, w.title, w.context_json, w.created_at
            FROM workflows w
            WHERE w.kind = 'upwork_contract'
              AND w.status IN ('queued', 'active', 'blocked')
            """
        ).fetchall()
        for contract in upwork_contracts:
            alert_key = f"upwork_deadline:{contract['id']}"
            already = connection.execute(
                "SELECT 1 FROM notification_log WHERE purpose = ? AND created_at >= datetime('now', '-6 hours') LIMIT 1",
                (alert_key,),
            ).fetchone()
            if already:
                continue
            try:
                import json as _json
                ctx = _json.loads(contract["context_json"]) if contract["context_json"] else {}
                deadline_hours = int(ctx.get("deadline_hours", 168))
                from datetime import datetime as _dt, timedelta as _td
                created = _dt.fromisoformat(contract["created_at"])
                deadline = created + _td(hours=deadline_hours)
                remaining = (deadline - now).total_seconds() / 3600
                if 0 < remaining < 24:
                    text = f"Upwork contract {contract['id']} due in {remaining:.1f}h: {contract['title'][:60]}"
                    from runtime.engine import send_telegram_message
                    from runtime.telegram_topics import resolve_notification_target
                    target = resolve_notification_target(connection, topic_key="ops-alerts")
                    if target:
                        send_telegram_message(
                            connection, text,
                            workflow_id=contract["id"],
                            purpose=alert_key,
                            chat_id=str(target.chat_id),
                            thread_id=target.thread_id,
                        )
                        alerts.append(alert_key)
            except Exception as exc:
                _log.error("Failed to check upwork deadline for %s: %s", contract["id"], exc)
    except Exception as exc:
        _log.error("Upwork deadline check failed: %s", exc)

    return alerts


def check_delegation_results(connection: sqlite3.Connection) -> list[str]:
    """Scan for completed subagent runs not yet relayed. Returns list of relayed run_ids."""
    relayed = []
    if not SUBAGENT_LOG_DIR.exists():
        return relayed

    for log_file in SUBAGENT_LOG_DIR.glob("sa_*.json"):
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        if data.get("status") != "completed":
            continue
        if data.get("relayed"):
            continue

        run_id = data.get("run_id", log_file.stem)
        agent = data.get("subagent", "unknown")
        task = data.get("task", "")[:100]
        output = data.get("output", "")[:500]

        text = f"Delegation complete: {agent} finished '{task}'\n{output}" if output else f"Delegation complete: {agent} finished '{task}'"

        try:
            from runtime.engine import send_telegram_message
            from runtime.telegram_topics import resolve_notification_target
            target = resolve_notification_target(connection, topic_key="ops-alerts")
            if target:
                send_telegram_message(
                    connection, text,
                    purpose=f"delegation:{run_id}",
                    chat_id=str(target.chat_id),
                    thread_id=target.thread_id,
                )
        except Exception as exc:
            _log.error("Failed to relay delegation result %s: %s", run_id, exc)
            continue

        # Mark as relayed
        data["relayed"] = True
        try:
            log_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        relayed.append(run_id)

    return relayed


# ---------------------------------------------------------------------------
# Tenant health digest + fleet weekly intelligence (added by 15-skills port)
# ---------------------------------------------------------------------------

def tenant_health_digest(connection: sqlite3.Connection) -> dict[str, Any]:
    """Generate health digest across all active tenants."""
    tenants = connection.execute(
        "SELECT * FROM tenants WHERE status = 'active' ORDER BY health_score ASC"
    ).fetchall()
    if not tenants:
        return {"tenants": 0, "alerts": []}

    alerts = []
    for tenant in tenants:
        if tenant["health_score"] < 60:
            alerts.append({
                "tenant_id": tenant["id"],
                "business_name": tenant["business_name"],
                "health_score": tenant["health_score"],
                "severity": "critical" if tenant["health_score"] < 40 else "warning",
            })

    digest = {
        "generated_at": _now_iso(),
        "total_tenants": len(tenants),
        "healthy": sum(1 for t in tenants if t["health_score"] >= 75),
        "at_risk": sum(1 for t in tenants if 40 <= t["health_score"] < 75),
        "critical": sum(1 for t in tenants if t["health_score"] < 40),
        "total_mrr": sum(t["monthly_value_usd"] for t in tenants),
        "alerts": alerts,
    }

    from runtime.engine import notify_operator, record_event, write_file
    path = DATA_ROOT / "operations" / "tenant-health-digest.json"
    write_file(path, json.dumps(digest, indent=2))
    record_event(connection, None, None, "tenant_health_digest", digest)

    if alerts:
        alert_text = f"Tenant health: {len(alerts)} alert(s)\n"
        for a in alerts[:5]:
            alert_text += f"- {a['business_name']}: score {a['health_score']} ({a['severity']})\n"
        notify_operator(connection, alert_text, purpose="ops")

    connection.commit()
    return digest


def fleet_weekly_intelligence(connection: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate anonymized cross-tenant intelligence into benchmarks."""
    import uuid
    from runtime.engine import notify_operator, record_event, write_file
    from runtime.llm import generate_text

    tenants = connection.execute(
        "SELECT * FROM tenants WHERE status = 'active'"
    ).fetchall()
    if len(tenants) < 1:
        return {"status": "insufficient_data", "tenant_count": len(tenants)}

    industries: dict[str, list] = {}
    for tenant in tenants:
        industry = tenant["industry"] or "general"
        industries.setdefault(industry, []).append(tenant)

    benchmarks = []
    for industry, group in industries.items():
        if len(group) < 2:
            continue
        scores = [t["health_score"] for t in group]
        values = [t["monthly_value_usd"] for t in group]
        benchmark_id = f"fb_{uuid.uuid4().hex[:12]}"
        stamp = _now_iso()

        connection.execute(
            "INSERT OR REPLACE INTO fleet_benchmarks (id, industry, metric_name, value, sample_size, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (benchmark_id, industry, "avg_health_score", sum(scores) / len(scores), len(scores), stamp),
        )
        benchmarks.append({"industry": industry, "avg_health": sum(scores) / len(scores), "count": len(group)})

    prompt = (
        "You are Rick, analyzing fleet-wide business intelligence.\n"
        f"Active tenants: {len(tenants)}\n"
        f"Industries: {json.dumps(benchmarks)}\n\n"
        "Generate a brief weekly intelligence summary with:\n"
        "1. Key trends across the fleet\n"
        "2. Industries performing above/below average\n"
        "3. Actionable insights for improving service delivery\n"
        "Keep it under 200 words."
    )
    result = generate_text("analysis", prompt, "Fleet intelligence: insufficient data for meaningful analysis this week.")

    report = {
        "generated_at": _now_iso(),
        "tenant_count": len(tenants),
        "benchmarks": benchmarks,
        "summary": result.text,
    }
    path = DATA_ROOT / "operations" / "fleet-intelligence.json"
    write_file(path, json.dumps(report, indent=2))
    record_event(connection, None, None, "fleet_weekly_intelligence", {"tenant_count": len(tenants), "benchmark_count": len(benchmarks)})
    connection.commit()
    return report


# ── Self-Push Loop ────────────────────────────────────────────────────────────

def self_push_loop(connection: sqlite3.Connection) -> list[str]:
    """
    Called on every heartbeat. Checks experiment queue, measures outcomes,
    and surfaces the next action. This is what makes Rick genuinely self-learning.
    """
    alerts: list[str] = []
    queue_path = DATA_ROOT / "experiments" / "queue.json"
    if not queue_path.exists():
        return alerts

    try:
        q = json.loads(queue_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("Failed to read experiment queue: %s", exc)
        return alerts

    from datetime import timezone, timedelta
    now = datetime.now(timezone.utc)
    changed = False

    for exp in q.get("items", []):
        status = exp.get("status", "queued")

        # Check if launched experiment has hit measure_at
        if status == "launched" and exp.get("measure_at"):
            try:
                measure_at = datetime.fromisoformat(exp["measure_at"].replace("Z", "+00:00"))
                if now >= measure_at:
                    exp["status"] = "measuring"
                    changed = True
                    alerts.append(
                        f"📊 Experiment ready to measure: '{exp.get('title',exp['id'])}' — "
                        f"check {exp.get('primary_metric',{}).get('source','manual')} and update queue."
                    )
            except (ValueError, KeyError) as exc:
                _log.warning("Failed to parse experiment measure_at for %s: %s", exp.get("id"), exc)

        # Surface any experiment that's been in measuring for 24h+ without result
        if status == "measuring" and exp.get("result") is None:
            alerts.append(
                f"⏰ Experiment '{exp.get('title', exp['id'])}' needs a result recorded — it's been measuring."
            )

    # Count active experiments and surface if all slots are empty
    active = [e for e in q.get("items", []) if e.get("status") in ("launched", "measuring")]
    if len(active) == 0:
        alerts.append(
            "⚡ No active experiments. Run experiment-engine.py --generate to queue new ones. "
            "Self-learning loop is idle."
        )

    if changed:
        q["updated_at"] = now.isoformat()
        queue_path.write_text(json.dumps(q, indent=2), encoding="utf-8")

    return alerts
